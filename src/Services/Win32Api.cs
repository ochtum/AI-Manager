using System.Runtime.InteropServices;
using System.Text;

namespace AI_CLI_Watcher.Services;

public static partial class Win32Api
{
    public const int SW_RESTORE = 9;
    public const int SW_SHOW = 5;
    public const int GW_OWNER = 4;
    public const int GWL_EXSTYLE = -20;
    public const uint WS_EX_TOOLWINDOW = 0x00000080;
    public const uint WS_EX_APPWINDOW = 0x00040000;
    public const uint GA_ROOT = 2;
    public const uint GA_ROOTOWNER = 3;
    public const byte VK_MENU = 0x12;
    public const uint KEYEVENTF_EXTENDEDKEY = 0x0001;
    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const uint ATTACH_PARENT_PROCESS = unchecked((uint)-1);
    public const uint PROCESS_QUERY_INFORMATION = 0x0400;
    public const uint PROCESS_VM_READ = 0x0010;

    public delegate bool EnumWindowsProc(nint hWnd, nint lParam);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool EnumWindows(EnumWindowsProc lpEnumFunc, nint lParam);

    [LibraryImport("user32.dll")]
    public static partial uint GetWindowThreadProcessId(nint hWnd, out uint lpdwProcessId);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool IsWindowVisible(nint hWnd);

    [LibraryImport("user32.dll", EntryPoint = "GetWindowTextW", StringMarshalling = StringMarshalling.Utf16)]
    public static partial int GetWindowText(nint hWnd, [Out] char[] lpString, int nMaxCount);

    [LibraryImport("user32.dll", EntryPoint = "GetWindowTextLengthW")]
    public static partial int GetWindowTextLength(nint hWnd);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool SetForegroundWindow(nint hWnd);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool ShowWindow(nint hWnd, int nCmdShow);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool IsIconic(nint hWnd);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool BringWindowToTop(nint hWnd);

    [LibraryImport("user32.dll")]
    public static partial nint GetWindow(nint hWnd, uint uCmd);

    [LibraryImport("user32.dll", EntryPoint = "GetWindowLongW")]
    public static partial int GetWindowLong(nint hWnd, int nIndex);

    [LibraryImport("user32.dll")]
    public static partial nint GetAncestor(nint hWnd, uint gaFlags);

    [LibraryImport("user32.dll")]
    public static partial nint SetActiveWindow(nint hWnd);

    [LibraryImport("user32.dll")]
    public static partial void keybd_event(byte bVk, byte bScan, uint dwFlags, nuint dwExtraInfo);

    [LibraryImport("user32.dll")]
    public static partial nint GetForegroundWindow();

    [LibraryImport("kernel32.dll")]
    public static partial uint GetCurrentThreadId();

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool FreeConsole();

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool AttachConsole(uint dwProcessId);

    [LibraryImport("kernel32.dll")]
    public static partial nint GetConsoleWindow();

    [LibraryImport("shcore.dll")]
    public static partial int SetProcessDpiAwareness(int awareness);

    [LibraryImport("kernel32.dll")]
    public static partial nint OpenProcess(uint dwDesiredAccess,
        [MarshalAs(UnmanagedType.Bool)] bool bInheritHandle, uint dwProcessId);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool CloseHandle(nint hObject);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool ReadProcessMemory(nint hProcess, nint lpBaseAddress,
        byte[] lpBuffer, nuint nSize, out nuint lpNumberOfBytesRead);

    [LibraryImport("ntdll.dll")]
    public static partial int NtQueryInformationProcess(nint processHandle,
        int processInformationClass, byte[] processInformation,
        int processInformationLength, out int returnLength);

    /// <summary>
    /// Reads the actual current working directory from a process's PEB
    /// via NtQueryInformationProcess + ReadProcessMemory (x64 only).
    /// </summary>
    public static string? GetProcessCurrentDirectory(int pid)
    {
        nint hProcess = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, false, (uint)pid);
        if (hProcess == 0) return null;
        try
        {
            // PROCESS_BASIC_INFORMATION: 6 x IntPtr = 48 bytes on x64
            byte[] pbi = new byte[48];
            int status = NtQueryInformationProcess(hProcess, 0, pbi, pbi.Length, out _);
            if (status != 0) return null;

            nint pebAddress = (nint)BitConverter.ToInt64(pbi, 8); // PebBaseAddress at offset 8
            if (pebAddress == 0) return null;

            // Read ProcessParameters pointer from PEB + 0x20
            byte[] ppBuf = new byte[8];
            if (!ReadProcessMemory(hProcess, pebAddress + 0x20, ppBuf, 8, out _))
                return null;
            nint processParams = (nint)BitConverter.ToInt64(ppBuf, 0);
            if (processParams == 0) return null;

            // Read CurrentDirectory UNICODE_STRING from RTL_USER_PROCESS_PARAMETERS + 0x38
            // UNICODE_STRING: Length(2) + MaximumLength(2) + padding(4) + Buffer(8) = 16 bytes
            byte[] usBuf = new byte[16];
            if (!ReadProcessMemory(hProcess, processParams + 0x38, usBuf, 16, out _))
                return null;

            ushort length = BitConverter.ToUInt16(usBuf, 0);
            nint buffer = (nint)BitConverter.ToInt64(usBuf, 8);
            if (length == 0 || buffer == 0) return null;

            byte[] strBuf = new byte[length];
            if (!ReadProcessMemory(hProcess, buffer, strBuf, (nuint)length, out _))
                return null;

            return Encoding.Unicode.GetString(strBuf).TrimEnd('\\');
        }
        catch { return null; }
        finally
        {
            CloseHandle(hProcess);
        }
    }

    public static string GetWindowTitle(nint hwnd)
    {
        int length = GetWindowTextLength(hwnd);
        if (length == 0) return "";
        char[] buf = new char[length + 1];
        GetWindowText(hwnd, buf, length + 1);
        return new string(buf, 0, length);
    }

    public static bool IsMainWindow(nint hwnd)
    {
        if (!IsWindowVisible(hwnd)) return false;
        int exStyle = GetWindowLong(hwnd, GWL_EXSTYLE);
        if ((exStyle & WS_EX_TOOLWINDOW) != 0 && (exStyle & WS_EX_APPWINDOW) == 0)
            return false;
        if (GetWindow(hwnd, GW_OWNER) != 0)
            return false;
        return true;
    }

    public static List<nint> FindWindowsForPid(uint pid)
    {
        var result = new List<nint>();
        EnumWindows((hwnd, _) =>
        {
            if (IsMainWindow(hwnd))
            {
                GetWindowThreadProcessId(hwnd, out uint procId);
                if (procId == pid)
                    result.Add(hwnd);
            }
            return true;
        }, 0);
        return result;
    }

    public static void ActivateWindow(nint hwnd)
    {
        var targets = new List<nint>();
        foreach (uint flag in new[] { GA_ROOTOWNER, GA_ROOT })
        {
            try
            {
                nint target = GetAncestor(hwnd, flag);
                if (target != 0 && !targets.Contains(target))
                    targets.Add(target);
            }
            catch { }
        }
        if (!targets.Contains(hwnd))
            targets.Add(hwnd);

        foreach (nint target in targets)
        {
            if (IsIconic(target))
                ShowWindow(target, SW_RESTORE);
            else
                ShowWindow(target, SW_SHOW);
        }

        keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0);
        try
        {
            foreach (nint target in targets)
            {
                SetForegroundWindow(target);
                BringWindowToTop(target);
                try { SetActiveWindow(target); } catch { }
            }
        }
        finally
        {
            keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0);
        }
    }

    public static nint? GetConsoleHwndForPid(uint pid)
    {
        try
        {
            FreeConsole();
            if (AttachConsole(pid))
            {
                nint hwnd = GetConsoleWindow();
                FreeConsole();
                AttachConsole(ATTACH_PARENT_PROCESS);
                if (hwnd != 0) return hwnd;
            }
            else
            {
                AttachConsole(ATTACH_PARENT_PROCESS);
            }
        }
        catch
        {
            try { AttachConsole(ATTACH_PARENT_PROCESS); } catch { }
        }
        return null;
    }
}
