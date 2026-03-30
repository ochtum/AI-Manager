using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using AI_CLI_Watcher.Models;

namespace AI_CLI_Watcher.Services;

public partial class WslScanner
{
    private readonly Dictionary<(string distro, int pid), (double time, int ticks)> _prevCpu = new();
    private readonly Dictionary<(string distro, int pid), long> _prevIo = new();
    private readonly Dictionary<(string distro, int pid), long> _headlessSince = new();
    private readonly Dictionary<string, int> _clkTck = new();
    private string? _defaultDistro;

    public bool ShowHeadlessWslProcesses { get; set; }
    public int HeadlessWslGraceSeconds { get; set; } = Constants.DefaultHeadlessWslGraceSeconds;

    /// <summary>
    /// Callback to resolve console HWND for a PID. Must be set to a UI-thread-safe
    /// implementation (e.g. via Dispatcher.Invoke) since GetConsoleHwndForPid uses
    /// FreeConsole/AttachConsole which modify process-global state.
    /// </summary>
    public Func<uint, nint?>? ConsoleHwndResolver { get; set; }

    public List<CliProcess> ScanWslProcesses()
    {
        var results = new List<CliProcess>();
        var observedKeys = new HashSet<(string distro, int pid)>();
        List<string> distros;
        try
        {
            distros = GetRunningDistros();
        }
        catch { return results; }

        var tabHostsByDistro = FindWslTabHosts();

        foreach (var distro in distros)
        {
            try
            {
                var psOutput = RunWslCommand(distro, "ps -eo pid=,ppid=,pcpu=,etimes=,tty=,comm=,args= -ww");
                if (string.IsNullOrEmpty(psOutput)) continue;

                var ttyAgeSeconds = new Dictionary<string, int>();
                var psRows = new List<PsRow>();
                var bestByTty = new Dictionary<(string cli, string tty), (int score, double cpu, int pid, CliProcess proc)>();

                foreach (var line in psOutput.Split('\n', StringSplitOptions.RemoveEmptyEntries))
                {
                    var parts = line.Split((char[]?)null, 7, StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length < 7) continue;

                    if (!int.TryParse(parts[0], out int wslPid)) continue;
                    if (!int.TryParse(parts[1], out int wslPpid)) continue;
                    if (!double.TryParse(parts[2], out double cpu)) continue;
                    if (!int.TryParse(parts[3], out int elapsed)) continue;

                    string tty = parts[4];
                    string exeName = parts[5];
                    string cmdlineStr = parts[6];

                    psRows.Add(new PsRow(wslPid, wslPpid, tty, exeName, cmdlineStr));

                    if (tty != "?" && !string.IsNullOrEmpty(tty))
                    {
                        if (!ttyAgeSeconds.TryGetValue(tty, out int prev) || elapsed > prev)
                            ttyAgeSeconds[tty] = elapsed;
                    }

                    if (string.IsNullOrEmpty(tty) || tty == "?") continue;
                    if (IsNonInteractiveCmdline(cmdlineStr)) continue;

                    var (displayName, matchScore) = MatchWslCli(exeName, cmdlineStr);
                    if (displayName == null) continue;

                    string status = cpu > Constants.CpuBusyThreshold ? "Processing" : "Waiting for input";
                    var procEntry = new CliProcess
                    {
                        Name = $"{displayName} (WSL:{distro})",
                        Pid = wslPid,
                        CpuPercent = cpu,
                        Status = status,
                        Cmdline = cmdlineStr,
                        TerminalType = $"WSL:{distro} ({tty})",
                        WslDistro = distro,
                        WslTty = tty,
                    };

                    var key = (displayName, tty);
                    var rank = (matchScore, cpu, wslPid);
                    if (!bestByTty.TryGetValue(key, out var current) ||
                        CompareRank(rank, (current.score, current.cpu, current.pid)) > 0)
                    {
                        bestByTty[key] = (matchScore, cpu, wslPid, procEntry);
                    }
                }

                var ttyProcs = bestByTty.Values.Select(v => (v.proc.WslTty, v.proc)).ToList();
                var pidsToResolve = ttyProcs.Select(t => t.proc.Pid).ToList();
                var procDetails = GetWslProcDetails(distro, pidsToResolve);

                foreach (var (tty, proc) in ttyProcs)
                {
                    if (procDetails.TryGetValue(proc.Pid, out var details))
                    {
                        proc.Cwd = details.cwd;
                        var (cpuPct, stat) = DetectWslStatus(distro, proc.Pid, proc.CpuPercent, details.ticks, details.ioTotal);
                        proc.CpuPercent = cpuPct;
                        proc.Status = stat;
                    }
                }

                // Resolve HWNDs for WSL tabs
                var liveTabTtys = CollectWslTabTtys(psRows);
                var ttyHwnds = new Dictionary<string, nint>();
                if (ttyProcs.Count > 0 && liveTabTtys.Count > 0)
                {
                    var targetTtys = ttyProcs.Select(t => t.WslTty).Where(t => !string.IsNullOrEmpty(t)).ToHashSet();
                    ttyHwnds = ResolveWslTtyHwnds(distro, targetTtys, liveTabTtys, ttyAgeSeconds, tabHostsByDistro.GetValueOrDefault(distro, []));
                }

                ttyProcs.Sort((a, b) =>
                {
                    int ageA = ttyAgeSeconds.GetValueOrDefault(a.WslTty, -1);
                    int ageB = ttyAgeSeconds.GetValueOrDefault(b.WslTty, -1);
                    int cmp = ageB.CompareTo(ageA);
                    if (cmp != 0) return cmp;
                    cmp = string.Compare(a.WslTty, b.WslTty, StringComparison.Ordinal);
                    if (cmp != 0) return cmp;
                    return a.proc.Pid.CompareTo(b.proc.Pid);
                });

                foreach (var (tty, proc) in ttyProcs)
                {
                    var key = (proc.WslDistro, proc.Pid);
                    observedKeys.Add(key);

                    if (ttyHwnds.TryGetValue(tty, out var hwnd))
                    {
                        proc.Hwnds = [hwnd];
                        _headlessSince.Remove(key);
                        results.Add(proc);
                        continue;
                    }

                    if (ShouldKeepHeadless(proc))
                        results.Add(proc);
                }
            }
            catch { }
        }

        CleanStaleEntries(observedKeys);
        return results;
    }

    private bool ShouldKeepHeadless(CliProcess proc)
    {
        if (ShowHeadlessWslProcesses) return true;

        int graceSeconds = Math.Max(0, HeadlessWslGraceSeconds);
        long now = Environment.TickCount64 / 1000;
        var key = (proc.WslDistro, proc.Pid);

        if (!_headlessSince.TryGetValue(key, out long firstSeen))
        {
            _headlessSince[key] = now;
            return true;
        }

        return (now - firstSeen) <= graceSeconds;
    }

    private static (string? displayName, int score) MatchWslCli(string exeName, string cmdline)
    {
        string exeLower = exeName.ToLowerInvariant();
        string cmdlineLower = cmdline.ToLowerInvariant();

        foreach (var def in CliDefinitions.Wsl)
        {
            if (def.CmdlineExclude.Any(ex => cmdlineLower.Contains(ex, StringComparison.OrdinalIgnoreCase)))
                continue;

            if (def.ExePatterns.Any(p => p.Equals(exeLower, StringComparison.OrdinalIgnoreCase)))
                return (def.DisplayName, 3);

            if (CliDefinitions.WslLauncherExes.Contains(exeLower))
            {
                if (def.CmdlineKeywords.Any(kw => cmdlineLower.Contains(kw, StringComparison.OrdinalIgnoreCase)))
                {
                    int score = exeLower is "node" or "bun" ? 2 : 1;
                    return (def.DisplayName, score);
                }
            }
        }
        return (null, 0);
    }

    private static bool IsNonInteractiveCmdline(string cmdline)
    {
        string cmd = $" {cmdline.ToLowerInvariant()} ";
        return CliDefinitions.NonInteractivePatterns.Any(p => cmd.Contains(p));
    }

    private List<string> GetRunningDistros()
    {
        var output = RunCommand("wsl", "--list --running --quiet");
        return output
            .Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .Select(d => d.Trim())
            .Where(d => !string.IsNullOrEmpty(d))
            .ToList();
    }

    private string GetDefaultDistro()
    {
        if (_defaultDistro != null) return _defaultDistro;
        try
        {
            var output = RunCommand("wsl", "--list --verbose");
            foreach (var line in output.Split('\n'))
            {
                string stripped = line.Trim();
                if (!stripped.StartsWith('*')) continue;
                var parts = Regex.Split(stripped[1..].Trim(), @"\s{2,}");
                if (parts.Length > 0 && !string.IsNullOrEmpty(parts[0]))
                {
                    _defaultDistro = parts[0].Trim();
                    return _defaultDistro;
                }
            }
        }
        catch { }
        _defaultDistro = "";
        return _defaultDistro;
    }

    private Dictionary<string, List<WslTabHost>> FindWslTabHosts()
    {
        var result = new Dictionary<string, List<WslTabHost>>();
        try
        {
            var processes = Process.GetProcessesByName("wsl");
            var wslProcs = new List<(Process proc, string distro)>();

            foreach (var proc in processes)
            {
                try
                {
                    string cmdLine = GetProcessCommandLine(proc.Id);
                    string cmdLower = cmdLine.ToLowerInvariant();
                    if (cmdLower.Contains("--exec") || Regex.IsMatch(cmdLower, @"(^|\s)-e(\s|$)"))
                        continue;

                    string distro = ParseWslDistroFromCmdline(cmdLine);
                    if (string.IsNullOrEmpty(distro)) continue;

                    try
                    {
                        var parent = GetParentProcess(proc);
                        if (parent?.ProcessName.Equals("wsl", StringComparison.OrdinalIgnoreCase) == true)
                            continue;
                    }
                    catch { }

                    wslProcs.Add((proc, distro));
                }
                catch { }
            }

            var seenFingerprints = new Dictionary<string, HashSet<(int, long)>>();
            foreach (var (wp, distro) in wslProcs)
            {
                try
                {
                    int targetPid = wp.Id;
                    var hostProc = wp;
                    try
                    {
                        var parent = GetParentProcess(wp);
                        if (parent?.ProcessName.Equals("cmd", StringComparison.OrdinalIgnoreCase) == true)
                        {
                            targetPid = parent.Id;
                            hostProc = parent;
                        }
                    }
                    catch { }

                    // Use ConsoleHwndResolver (UI-thread-safe) if available,
                    // otherwise fall back to FindWindowsForPid
                    nint? hwnd = ConsoleHwndResolver?.Invoke((uint)targetPid);
                    if (!hwnd.HasValue)
                    {
                        var hwnds = Win32Api.FindWindowsForPid((uint)targetPid);
                        hwnd = hwnds.Count > 0 ? hwnds[0] : null;
                    }
                    if (hwnd.HasValue)
                    {
                        long startedMs = (long)(hostProc.StartTime.ToUniversalTime() - DateTime.UnixEpoch).TotalMilliseconds;
                        var fingerprint = (hostProc.Id, startedMs);
                        var fps = seenFingerprints.GetValueOrDefault(distro, []);
                        if (fps.Contains(fingerprint)) continue;
                        fps.Add(fingerprint);
                        seenFingerprints[distro] = fps;

                        var host = new WslTabHost(distro, hwnd.Value, hostProc.Id, startedMs);
                        if (!result.ContainsKey(distro))
                            result[distro] = [];
                        result[distro].Add(host);
                    }
                }
                catch { }
            }

            foreach (var hosts in result.Values)
                hosts.Sort((a, b) => a.StartedMs.CompareTo(b.StartedMs));
        }
        catch { }
        return result;
    }

    private string ParseWslDistroFromCmdline(string cmdline)
    {
        var parts = cmdline.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        for (int i = 0; i < parts.Length; i++)
        {
            string lower = parts[i].ToLowerInvariant();
            if ((lower is "-d" or "--distribution") && i + 1 < parts.Length)
                return parts[i + 1].Trim();
            if (lower.StartsWith("--distribution="))
                return parts[i].Split('=', 2)[1].Trim();
            if (lower.StartsWith("-d") && parts[i].Length > 2)
                return parts[i][2..].Trim();
        }
        return GetDefaultDistro();
    }

    private Dictionary<int, (string cwd, int? ticks, long? ioTotal)> GetWslProcDetails(string distro, List<int> pids)
    {
        var details = new Dictionary<int, (string cwd, int? ticks, long? ioTotal)>();
        if (pids.Count == 0) return details;

        string pyCode = """
            import os, sys
            for raw_pid in sys.argv[1:]:
                pid = int(raw_pid)
                cwd = ''
                try:
                    cwd = os.readlink(f'/proc/{pid}/cwd')
                except Exception:
                    pass
                ticks = ''
                try:
                    with open(f'/proc/{pid}/stat', 'r', encoding='utf-8', errors='replace') as fh:
                        stat_line = fh.read().strip()
                    after_comm = stat_line[stat_line.rfind(')') + 2:].split()
                    ticks = str(int(after_comm[11]) + int(after_comm[12]))
                except Exception:
                    pass
                io_total = ''
                try:
                    values = {}
                    with open(f'/proc/{pid}/io', 'r', encoding='utf-8', errors='replace') as fh:
                        for line in fh:
                            key, value = line.split(':', 1)
                            values[key.strip()] = int(value.strip() or 0)
                    io_total = str(
                        values.get('rchar', 0)
                        + values.get('wchar', 0)
                        + values.get('read_bytes', 0)
                        + values.get('write_bytes', 0)
                    )
                except Exception:
                    pass
                print(f'{pid}\t{cwd}\t{ticks}\t{io_total}')
            """;

        string pidArgs = string.Join(" ", pids);
        try
        {
            var output = RunWslCommand(distro, $"python3 -c \"{pyCode.Replace("\"", "\\\"")}\" {pidArgs}");
            if (string.IsNullOrEmpty(output)) return details;

            foreach (var line in output.Split('\n', StringSplitOptions.RemoveEmptyEntries))
            {
                var tabParts = line.Split('\t', 4);
                if (tabParts.Length != 4) continue;
                if (!int.TryParse(tabParts[0].Trim(), out int pid)) continue;
                int? ticks = int.TryParse(tabParts[2].Trim(), out int t) ? t : null;
                long? ioTotal = long.TryParse(tabParts[3].Trim(), out long io) ? io : null;
                details[pid] = (tabParts[1].Trim(), ticks, ioTotal);
            }
        }
        catch { }
        return details;
    }

    private (double cpu, string status) DetectWslStatus(string distro, int pid, double fallbackCpu, int? procTicks, long? ioTotal)
    {
        var key = (distro, pid);
        double cpuPercent = fallbackCpu;

        if (procTicks.HasValue)
        {
            double now = Environment.TickCount64 / 1000.0;
            if (_prevCpu.TryGetValue(key, out var prev))
            {
                double elapsed = now - prev.time;
                int deltaTicks = procTicks.Value - prev.ticks;
                if (elapsed > 0 && deltaTicks >= 0)
                {
                    int clkTck = GetClkTck(distro);
                    cpuPercent = Math.Max(0, (double)deltaTicks / clkTck / elapsed * 100.0);
                }
            }
            _prevCpu[key] = (now, procTicks.Value);
        }

        long ioDelta = 0;
        if (ioTotal.HasValue)
        {
            if (_prevIo.TryGetValue(key, out long prevIo) && ioTotal.Value >= prevIo)
                ioDelta = ioTotal.Value - prevIo;
            _prevIo[key] = ioTotal.Value;
        }

        string status = (cpuPercent > Constants.CpuBusyThreshold || ioDelta > Constants.IoBusyThreshold)
            ? "Processing" : "Waiting for input";
        return (cpuPercent, status);
    }

    private int GetClkTck(string distro)
    {
        if (_clkTck.TryGetValue(distro, out int cached)) return cached;
        int clkTck = 100;
        try
        {
            string output = RunWslCommand(distro, "getconf CLK_TCK");
            if (int.TryParse(output.Trim(), out int val) && val > 0)
                clkTck = val;
        }
        catch { }
        _clkTck[distro] = clkTck;
        return clkTck;
    }

    private static HashSet<string> CollectWslTabTtys(List<PsRow> psRows)
    {
        var ppidByPid = psRows.ToDictionary(r => r.Pid, r => r.Ppid);
        var commByPid = psRows.ToDictionary(r => r.Pid, r => r.Comm);
        var liveTtys = new HashSet<string>();

        foreach (var row in psRows)
        {
            if (string.IsNullOrEmpty(row.Tty) || row.Tty == "?") continue;
            if (!HasWslRelayAncestor(row.Pid, ppidByPid, commByPid)) continue;
            if (CliDefinitions.WslTabTtyProcessNames.Contains(row.Comm) ||
                MatchWslCli(row.Comm, row.Args).displayName != null)
            {
                liveTtys.Add(row.Tty);
            }
        }
        return liveTtys;
    }

    private static bool HasWslRelayAncestor(int pid, Dictionary<int, int> ppidByPid, Dictionary<int, string> commByPid)
    {
        var visited = new HashSet<int>();
        int? current = ppidByPid.GetValueOrDefault(pid);
        while (current.HasValue && current.Value > 0 && visited.Add(current.Value))
        {
            if (commByPid.GetValueOrDefault(current.Value, "").StartsWith("Relay("))
                return true;
            current = ppidByPid.ContainsKey(current.Value) ? ppidByPid[current.Value] : null;
        }
        return false;
    }

    private static Dictionary<string, nint> ResolveWslTtyHwnds(
        string distro,
        HashSet<string> targetTtys,
        HashSet<string> liveTtys,
        Dictionary<string, int> ttyAgeSeconds,
        List<WslTabHost> tabHosts)
    {
        var orderedLiveTtys = liveTtys
            .OrderByDescending(tty => ttyAgeSeconds.GetValueOrDefault(tty, -1))
            .ThenBy(TtySortKey)
            .ToList();

        var hostByFingerprint = tabHosts.ToDictionary(h => (h.HostPid, h.StartedMs));
        var resolved = new Dictionary<string, nint>();
        var usedFingerprints = new HashSet<(int, long)>();

        // Simple assignment: match ttys to hosts by order
        int count = Math.Min(orderedLiveTtys.Count, tabHosts.Count);
        for (int i = 0; i < count; i++)
        {
            string tty = orderedLiveTtys[i];
            var host = tabHosts[i];
            usedFingerprints.Add((host.HostPid, host.StartedMs));
            if (targetTtys.Contains(tty))
                resolved[tty] = host.Hwnd;
        }

        return resolved;
    }

    private static (int, int, string) TtySortKey(string tty)
    {
        string lower = tty.ToLowerInvariant();
        if (lower.StartsWith("pts/") && int.TryParse(lower[4..], out int num))
            return (0, num, lower);
        return (1, 0, lower);
    }

    private void CleanStaleEntries(HashSet<(string distro, int pid)> observedKeys)
    {
        foreach (var key in _prevCpu.Keys.Where(k => !observedKeys.Contains(k)).ToList())
            _prevCpu.Remove(key);
        foreach (var key in _prevIo.Keys.Where(k => !observedKeys.Contains(k)).ToList())
            _prevIo.Remove(key);
        foreach (var key in _headlessSince.Keys.Where(k => !observedKeys.Contains(k)).ToList())
            _headlessSince.Remove(key);
    }

    private static string RunWslCommand(string distro, string command)
    {
        return RunCommand("wsl", $"-d {distro} -- {command}");
    }

    private static string RunCommand(string fileName, string arguments)
    {
        try
        {
            using var proc = new Process();
            proc.StartInfo = new ProcessStartInfo
            {
                FileName = fileName,
                Arguments = arguments,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                StandardOutputEncoding = null,
            };
            proc.Start();

            // Read with timeout to avoid hanging on subprocess
            var readTask = Task.Run(() => ReadAllBytes(proc.StandardOutput.BaseStream));
            if (!readTask.Wait(TimeSpan.FromSeconds(8)))
            {
                try { proc.Kill(); } catch { }
                return "";
            }
            var rawBytes = readTask.Result;
            if (!proc.WaitForExit(3000))
                try { proc.Kill(); } catch { }
            return DecodeWslOutput(rawBytes);
        }
        catch { return ""; }
    }

    private static byte[] ReadAllBytes(Stream stream)
    {
        using var ms = new MemoryStream();
        stream.CopyTo(ms);
        return ms.ToArray();
    }

    private static string DecodeWslOutput(byte[] raw)
    {
        if (raw.Length == 0) return "";
        if (raw.Any(b => b == 0))
            return Encoding.Unicode.GetString(raw).Replace("\0", "");
        return Encoding.UTF8.GetString(raw);
    }

    private static string GetProcessCommandLine(int pid)
    {
        try
        {
            var (cmdLine, _) = Win32Api.GetProcessPebStrings(pid);
            return cmdLine ?? "";
        }
        catch { }
        return "";
    }

    private static Process? GetParentProcess(Process proc)
    {
        try
        {
            // Read PROCESS_BASIC_INFORMATION to get InheritedFromUniqueProcessId
            nint hProcess = Win32Api.OpenProcess(
                Win32Api.PROCESS_QUERY_INFORMATION, false, (uint)proc.Id);
            if (hProcess == 0) return null;
            try
            {
                byte[] pbi = new byte[48];
                int status = Win32Api.NtQueryInformationProcess(hProcess, 0, pbi, pbi.Length, out _);
                if (status != 0) return null;
                int ppid = (int)BitConverter.ToInt64(pbi, 40);
                return Process.GetProcessById(ppid);
            }
            finally { Win32Api.CloseHandle(hProcess); }
        }
        catch { }
        return null;
    }

    private static int CompareRank((int score, double cpu, int pid) a, (int score, double cpu, int pid) b)
    {
        int cmp = a.score.CompareTo(b.score);
        if (cmp != 0) return cmp;
        cmp = a.cpu.CompareTo(b.cpu);
        if (cmp != 0) return cmp;
        return a.pid.CompareTo(b.pid);
    }

    public record PsRow(int Pid, int Ppid, string Tty, string Comm, string Args);
    public record WslTabHost(string Distro, nint Hwnd, int HostPid, long StartedMs);
}
