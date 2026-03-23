using System.Diagnostics;
using System.IO;
using AI_CLI_Watcher.Models;

namespace AI_CLI_Watcher.Services;

public class ProcessScanner
{
    private readonly Dictionary<int, long> _prevIo = new();
    private readonly Dictionary<int, nint> _hwndCache = new();

    /// <summary>
    /// Callback to resolve console HWND for a PID via AttachConsole/GetConsoleWindow.
    /// Must run on the UI thread (set via Dispatcher.Invoke in MainWindow).
    /// </summary>
    public Func<uint, nint?>? ConsoleHwndResolver { get; set; }

    public List<CliProcess> ScanWindowsProcesses()
    {
        var results = new List<CliProcess>();
        var seenPids = new HashSet<int>();

        // Single WMI query for ALL process data
        var snapshot = GetProcessSnapshot();
        var byPid = snapshot.ToDictionary(p => p.Pid);
        var childrenOf = snapshot.GroupBy(p => p.ParentPid)
            .ToDictionary(g => g.Key, g => g.ToList());

        foreach (var info in snapshot)
        {
            try
            {
                if (string.IsNullOrEmpty(info.CommandLine)) continue;
                if (IsNonInteractiveCmdline(info.CommandLine)) continue;

                string? displayName = MatchCli(info);
                if (displayName == null) continue;

                int pid = info.Pid;
                if (!seenPids.Add(pid)) continue;

                if (HasCliAncestor(pid, displayName, byPid)) continue;
                if (IsWindowsCliWrapper(info, displayName) && HasSameCliChild(pid, displayName, childrenOf, byPid))
                    continue;

                var (cpu, status) = DetectStatus(info, childrenOf);
                string cwd = GetWorkingDirectory(info);

                var (terminalPid, terminalName, terminalType) = FindTerminalAncestor(pid, byPid);

                // Skip Claude desktop app
                if (displayName == "Claude Code" &&
                    info.ExeName.Equals("claude.exe", StringComparison.OrdinalIgnoreCase) &&
                    terminalName.Equals("explorer.exe", StringComparison.OrdinalIgnoreCase))
                    continue;

                var hwnds = new List<nint>();
                if (terminalPid.HasValue)
                    hwnds = Win32Api.FindWindowsForPid((uint)terminalPid.Value);
                if (hwnds.Count == 0)
                    hwnds = Win32Api.FindWindowsForPid((uint)pid);
                if (hwnds.Count == 0 && byPid.ContainsKey(info.ParentPid))
                    hwnds = Win32Api.FindWindowsForPid((uint)info.ParentPid);

                if (hwnds.Count == 0)
                {
                    results.Add(new CliProcess
                    {
                        Name = displayName,
                        Pid = pid,
                        CpuPercent = cpu,
                        Status = status,
                        Cmdline = info.CommandLine,
                        Cwd = cwd,
                        TerminalPid = terminalPid,
                        TerminalType = string.IsNullOrEmpty(terminalType) ? "(no window)" : $"{terminalType} (no window)",
                        Hwnds = [],
                    });
                    continue;
                }

                results.Add(new CliProcess
                {
                    Name = displayName,
                    Pid = pid,
                    CpuPercent = cpu,
                    Status = status,
                    Cmdline = info.CommandLine,
                    Cwd = cwd,
                    TerminalPid = terminalPid,
                    TerminalType = terminalType,
                    Hwnds = hwnds,
                });
            }
            catch { }
        }

        ResolveHwnds(results, byPid);
        results.RemoveAll(p =>
            p.Hwnds.Count == 0
                ? !IsHeadlessDisplayAllowed(p.Name, p.Cwd)
                : !p.Hwnds.Any(h => Win32Api.IsWindow(h))
                  || p.Hwnds.All(h => Win32Api.IsOrphanedConsoleHwnd(h)));
        RemoveDuplicateHwndDescendants(results, byPid);
        CleanStaleIo(results.Select(p => p.Pid).ToHashSet());
        return results;
    }

    private void ResolveHwnds(List<CliProcess> procs, Dictionary<int, ProcessInfo> byPid)
    {
        var livePids = procs.Select(p => p.Pid).ToHashSet();
        foreach (var pid in _hwndCache.Keys.Where(k => !livePids.Contains(k)).ToList())
            _hwndCache.Remove(pid);
        // Invalidate cached HWNDs that are no longer valid windows
        foreach (var pid in _hwndCache.Keys.Where(k => !Win32Api.IsWindow(_hwndCache[k])).ToList())
            _hwndCache.Remove(pid);

        foreach (var p in procs)
        {
            if (p.Name.Contains("(WSL:"))
            {
                if (p.Hwnds.Count > 0) p.Hwnds = [p.Hwnds[0]];
                continue;
            }

            if (_hwndCache.TryGetValue(p.Pid, out var cached))
            {
                p.Hwnds = [cached];
                continue;
            }

            // Use AttachConsole/GetConsoleWindow (via UI-thread resolver) to find
            // the exact console HWND. This is critical for Windows Terminal where
            // EnumWindows can't map processes to their specific tab.
            nint? hwnd = ConsoleHwndResolver?.Invoke((uint)p.Pid);
            if ((!hwnd.HasValue || hwnd.Value == 0) && p.TerminalPid.HasValue)
                hwnd = ConsoleHwndResolver?.Invoke((uint)p.TerminalPid.Value);
            if ((!hwnd.HasValue || hwnd.Value == 0) && byPid.TryGetValue(p.Pid, out var info))
                hwnd = ConsoleHwndResolver?.Invoke((uint)info.ParentPid);

            if (hwnd.HasValue && hwnd.Value != 0)
            {
                _hwndCache[p.Pid] = hwnd.Value;
                p.Hwnds = [hwnd.Value];
            }
            else if (p.Hwnds.Count > 0)
            {
                // Fallback to EnumWindows result
                _hwndCache[p.Pid] = p.Hwnds[0];
                p.Hwnds = [p.Hwnds[0]];
            }
        }
    }

    /// <summary>
    /// If multiple detected CLI processes share the same resolved HWND,
    /// remove any that are descendants of another in the same group.
    /// This filters out child processes that inherited a parent CLI's console
    /// (e.g., copilot.exe spawned by claude.exe sharing the same terminal tab).
    /// </summary>
    private static void RemoveDuplicateHwndDescendants(List<CliProcess> procs, Dictionary<int, ProcessInfo> byPid)
    {
        var byHwnd = procs.Where(p => p.Hwnds.Count > 0)
            .GroupBy(p => p.Hwnds[0])
            .Where(g => g.Count() > 1);

        var toRemove = new HashSet<int>();
        foreach (var group in byHwnd)
        {
            var pidsInGroup = group.Select(p => p.Pid).ToHashSet();
            foreach (var proc in group)
            {
                if (IsDescendantOfAny(proc.Pid, pidsInGroup, byPid, group))
                    toRemove.Add(proc.Pid);
            }
        }

        if (toRemove.Count > 0)
            procs.RemoveAll(p => toRemove.Contains(p.Pid));
    }

    private static bool IsDescendantOfAny(
        int pid,
        HashSet<int> candidatePids,
        Dictionary<int, ProcessInfo> byPid,
        IEnumerable<CliProcess> group)
    {
        var cliNameByPid = group.ToDictionary(p => p.Pid, p => p.Name);
        if (!cliNameByPid.TryGetValue(pid, out var selfCliName)) return false;

        if (!byPid.TryGetValue(pid, out var self)) return false;
        var visited = new HashSet<int>();
        int current = self.ParentPid;
        while (current > 4 && visited.Add(current))
        {
            if (candidatePids.Contains(current))
            {
                // Deduplicate only when the descendant belongs to the same CLI type.
                // Keep different CLI tools visible even if they share one terminal tab.
                if (cliNameByPid.TryGetValue(current, out var ancestorCliName) &&
                    string.Equals(selfCliName, ancestorCliName, StringComparison.Ordinal))
                    return true;
            }
            if (byPid.TryGetValue(current, out var parent))
                current = parent.ParentPid;
            else
                break;
        }
        return false;
    }

    private static string? MatchCli(ProcessInfo info)
    {
        string exeName = info.ExeName.ToLowerInvariant();
        string cmdlineLower = info.CommandLine.ToLowerInvariant();
        string exePathLower = info.ExePath.ToLowerInvariant();

        foreach (var def in CliDefinitions.Windows)
        {
            if (def.CmdlineExclude.Any(ex => cmdlineLower.Contains(ex, StringComparison.OrdinalIgnoreCase)))
                continue;

            if (def.ExePatterns.Any(p => p.Equals(exeName, StringComparison.OrdinalIgnoreCase)))
            {
                if (def.PathKeywords.Length > 0 &&
                    !def.PathKeywords.Any(pk => exePathLower.Contains(pk, StringComparison.OrdinalIgnoreCase)))
                    continue;
                return def.DisplayName;
            }

            if (exeName is "node.exe" or "node" or "npm.exe" or "npm" or "npx.exe" or "npx")
            {
                if (def.CmdlineKeywords.Any(kw => cmdlineLower.Contains(kw, StringComparison.OrdinalIgnoreCase)))
                    return def.DisplayName;
            }
        }
        return null;
    }

    private static bool IsWindowsCliWrapper(ProcessInfo info, string? displayName = null)
    {
        if (!CliDefinitions.WindowsWrapperExes.Contains(info.ExeName)) return false;
        string matched = displayName ?? MatchCli(info) ?? "";
        if (string.IsNullOrEmpty(matched)) return false;

        string cmdlineLower = info.CommandLine.ToLowerInvariant();
        foreach (var def in CliDefinitions.Windows)
        {
            if (def.DisplayName != matched) continue;
            if (def.CmdlineExclude.Any(ex => cmdlineLower.Contains(ex, StringComparison.OrdinalIgnoreCase)))
                return false;
            return def.CmdlineKeywords.Any(kw => cmdlineLower.Contains(kw, StringComparison.OrdinalIgnoreCase));
        }
        return false;
    }

    private static bool IsNonInteractiveCmdline(string cmdline)
    {
        string cmd = $" {cmdline.ToLowerInvariant()} ";
        return CliDefinitions.NonInteractivePatterns.Any(p => cmd.Contains(p));
    }

    private static bool HasCliAncestor(int pid, string displayName, Dictionary<int, ProcessInfo> byPid)
    {
        var visited = new HashSet<int>();
        if (!byPid.TryGetValue(pid, out var self)) return false;
        int current = self.ParentPid;
        while (current > 4 && visited.Add(current))
        {
            if (byPid.TryGetValue(current, out var parentInfo))
            {
                string? matched = MatchCli(parentInfo);
                if (matched == displayName && !IsWindowsCliWrapper(parentInfo, displayName))
                    return true;
                current = parentInfo.ParentPid;
            }
            else break;
        }
        return false;
    }

    /// <summary>
    /// Returns true if any ancestor process is a CLI process of ANY type.
    /// Filters out subprocesses spawned by other CLI tools (e.g., copilot.exe spawned by Claude's node.exe).
    /// </summary>
    private static bool HasAnyCliAncestor(int pid, Dictionary<int, ProcessInfo> byPid)
    {
        var visited = new HashSet<int>();
        if (!byPid.TryGetValue(pid, out var self)) return false;
        int current = self.ParentPid;
        while (current > 4 && visited.Add(current))
        {
            if (byPid.TryGetValue(current, out var parentInfo))
            {
                string? matched = MatchCli(parentInfo);
                if (matched != null && !IsWindowsCliWrapper(parentInfo, matched))
                    return true;
                current = parentInfo.ParentPid;
            }
            else break;
        }
        return false;
    }

    private static bool HasSameCliChild(int pid, string displayName,
        Dictionary<int, List<ProcessInfo>> childrenOf, Dictionary<int, ProcessInfo> byPid)
    {
        if (!childrenOf.TryGetValue(pid, out var directChildren)) return false;

        // Check direct children and their descendants
        var toCheck = new Queue<ProcessInfo>(directChildren);
        var visited = new HashSet<int>();
        while (toCheck.Count > 0)
        {
            var child = toCheck.Dequeue();
            if (!visited.Add(child.Pid)) continue;

            string? matched = MatchCli(child);
            if (matched == displayName && !IsWindowsCliWrapper(child, displayName))
                return true;

            if (childrenOf.TryGetValue(child.Pid, out var grandchildren))
                foreach (var gc in grandchildren) toCheck.Enqueue(gc);
        }
        return false;
    }

    private (double cpu, string status) DetectStatus(ProcessInfo info,
        Dictionary<int, List<ProcessInfo>> childrenOf)
    {
        double treeCpu = GetTreeCpu(info.Pid, childrenOf);
        long currentIo = GetTreeIoFromSnapshot(info.Pid, childrenOf);

        long prev = _prevIo.GetValueOrDefault(info.Pid, -1);
        _prevIo[info.Pid] = currentIo;
        long ioDelta = prev >= 0 ? currentIo - prev : 0;

        bool isBusy = treeCpu > Constants.CpuBusyThreshold || ioDelta > Constants.IoBusyThreshold;
        return (treeCpu, isBusy ? "Processing" : "Waiting for input");
    }

    private static double GetTreeCpu(int pid, Dictionary<int, List<ProcessInfo>> childrenOf)
    {
        double total = 0;
        try
        {
            using var proc = Process.GetProcessById(pid);
            proc.Refresh();
            total += proc.TotalProcessorTime.TotalMilliseconds > 0 ? 0.5 : 0.0;
        }
        catch { }

        if (childrenOf.TryGetValue(pid, out var children))
        {
            foreach (var child in children)
            {
                try
                {
                    using var proc = Process.GetProcessById(child.Pid);
                    proc.Refresh();
                    total += proc.TotalProcessorTime.TotalMilliseconds > 0 ? 0.1 : 0.0;
                }
                catch { }
            }
        }
        return total;
    }

    private static long GetTreeIoFromSnapshot(int pid, Dictionary<int, List<ProcessInfo>> childrenOf)
    {
        long total = 0;
        var toVisit = new Queue<int>();
        toVisit.Enqueue(pid);
        var visited = new HashSet<int>();

        while (toVisit.Count > 0)
        {
            int current = toVisit.Dequeue();
            if (!visited.Add(current)) continue;

            try
            {
                using var proc = Process.GetProcessById(current);
                // Use process handle for IO counters via GetProcessIoCounters
                total += GetProcessIo(proc);
            }
            catch { }

            if (childrenOf.TryGetValue(current, out var children))
                foreach (var c in children) toVisit.Enqueue(c.Pid);
        }
        return total;
    }

    private static long GetProcessIo(Process proc)
    {
        try
        {
            // Access via performance counters or handle - simplified
            return proc.WorkingSet64; // Approximation for activity detection
        }
        catch { return 0; }
    }

    private static string GetWorkingDirectory(ProcessInfo info)
    {
        // Read actual CWD from PEB via NtQueryInformationProcess
        try
        {
            string? cwd = Win32Api.GetProcessCurrentDirectory(info.Pid);
            if (!string.IsNullOrEmpty(cwd))
                return cwd;
        }
        catch { }

        // Fallback: extract from exe path
        if (!string.IsNullOrEmpty(info.ExePath))
        {
            try { return Path.GetDirectoryName(info.ExePath) ?? ""; } catch { }
        }
        return "";
    }

    private static (int? terminalPid, string terminalName, string terminalType)
        FindTerminalAncestor(int pid, Dictionary<int, ProcessInfo> byPid)
    {
        if (!byPid.TryGetValue(pid, out var self)) return (null, "", "");

        var visited = new HashSet<int>();
        int current = self.ParentPid;
        while (current > 4 && visited.Add(current))
        {
            var hwnds = Win32Api.FindWindowsForPid((uint)current);
            if (hwnds.Count > 0)
            {
                string procName = byPid.TryGetValue(current, out var pi) ? pi.ExeName : "";
                // Remove .exe extension for process name
                if (procName.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                    procName = procName[..^4];

                string termType = CliDefinitions.TerminalLabels
                    .GetValueOrDefault(procName + ".exe", procName);
                return (current, procName, termType);
            }
            if (byPid.TryGetValue(current, out var parent))
                current = parent.ParentPid;
            else
                break;
        }
        return (null, "", "");
    }

    private void CleanStaleIo(HashSet<int> livePids)
    {
        foreach (var pid in _prevIo.Keys.Where(k => !livePids.Contains(k)).ToList())
            _prevIo.Remove(pid);
    }

    /// <summary>
    /// Fast process snapshot via Toolhelp32 + PEB reads (replaces slow WMI query).
    /// Only reads CommandLine/ExePath for processes matching known CLI exe names.
    /// </summary>
    private static List<ProcessInfo> GetProcessSnapshot()
    {
        var allProcs = Win32Api.GetAllProcesses();

        // Build set of exe names that could be CLI tools
        var interestingNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var def in CliDefinitions.Windows)
            foreach (var p in def.ExePatterns)
                interestingNames.Add(p);
        foreach (var name in CliDefinitions.WindowsWrapperExes)
            interestingNames.Add(name);

        var result = new List<ProcessInfo>(allProcs.Count);
        foreach (var (pid, parentPid, exeName) in allProcs)
        {
            if (pid == 0) continue;

            string cmdLine = "";
            string exePath = "";

            if (interestingNames.Contains(exeName))
            {
                try
                {
                    var (cl, ip) = Win32Api.GetProcessPebStrings((int)pid);
                    cmdLine = cl ?? "";
                    exePath = ip ?? "";
                }
                catch { }
            }

            result.Add(new ProcessInfo((int)pid, (int)parentPid, exeName, cmdLine, exePath));
        }

        return result;
    }

    public record ProcessInfo(int Pid, int ParentPid, string ExeName, string CommandLine, string ExePath);

    private static bool IsHeadlessDisplayAllowed(string cliName, string cwd)
    {
        if (!string.Equals(cliName, "GitHub Copilot CLI", StringComparison.Ordinal))
            return false;
        return !string.IsNullOrWhiteSpace(cwd);
    }
}
