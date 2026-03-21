namespace AI_CLI_Watcher.Models;

public class CliProcess
{
    public string Name { get; set; } = "";
    public int Pid { get; set; }
    public double CpuPercent { get; set; }
    public string Status { get; set; } = "Waiting for input";
    public string Cmdline { get; set; } = "";
    public string Cwd { get; set; } = "";
    public int? TerminalPid { get; set; }
    public string TerminalType { get; set; } = "";
    public List<nint> Hwnds { get; set; } = [];
    public string WslDistro { get; set; } = "";
    public string WslTty { get; set; } = "";

    public bool IsProcessing => Status == "Processing";
}
