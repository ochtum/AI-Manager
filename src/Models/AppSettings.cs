using System.Text.Json.Serialization;

namespace AI_CLI_Watcher.Models;

public class AppSettings
{
    [JsonPropertyName("always_on_top")]
    public bool AlwaysOnTop { get; set; }

    [JsonPropertyName("layout_mode")]
    public string LayoutMode { get; set; } = "landscape";

    [JsonPropertyName("window_geometries")]
    public Dictionary<string, string> WindowGeometries { get; set; } = new();

    [JsonPropertyName("process_labels")]
    public Dictionary<string, ProcessLabel> ProcessLabels { get; set; } = new();

    [JsonPropertyName("wsl_terminal_assignments")]
    public Dictionary<string, Dictionary<string, WslTerminalHost>> WslTerminalAssignments { get; set; } = new();

    public static AppSettings CreateDefault() => new()
    {
        AlwaysOnTop = false,
        LayoutMode = "landscape",
        WindowGeometries = new Dictionary<string, string>
        {
            ["landscape"] = Constants.LandscapeGeometry,
            ["portrait"] = Constants.PortraitGeometry,
            ["minimized"] = Constants.MinimizedGeometry,
        },
        ProcessLabels = new(),
        WslTerminalAssignments = new(),
    };
}

public class ProcessLabel
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("color")]
    public string Color { get; set; } = "";
}

public class WslTerminalHost
{
    [JsonPropertyName("host_pid")]
    public int HostPid { get; set; }

    [JsonPropertyName("host_started_ms")]
    public long HostStartedMs { get; set; }
}

public static class Constants
{
    public const int RefreshIntervalMs = 2000;
    public const double CpuBusyThreshold = 2.0;
    public const int IoBusyThreshold = 1000;
    public const int GeometrySaveDelayMs = 450;

    public const string LandscapeGeometry = "1200x420+100+100";
    public const string PortraitGeometry = "320x760+100+100";
    public const string MinimizedGeometry = "220x90+100+100";

    public const int LandscapeMinWidth = 900;
    public const int LandscapeMinHeight = 320;
    public const int PortraitMinWidth = 280;
    public const int PortraitMinHeight = 460;
    public const int MinimizedMinWidth = 220;
    public const int MinimizedMinHeight = 90;
}
