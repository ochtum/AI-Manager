using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;
using AI_CLI_Watcher.Models;

namespace AI_CLI_Watcher.Services;

public partial class SettingsService
{
    private readonly string _settingsPath;
    private AppSettings _settings;

    public AppSettings Settings => _settings;

    public SettingsService(string settingsPath)
    {
        _settingsPath = settingsPath;
        _settings = Load();
    }

    public void Save()
    {
        try
        {
            var json = JsonSerializer.Serialize(_settings, new JsonSerializerOptions
            {
                WriteIndented = true,
            });
            File.WriteAllText(_settingsPath, json);
        }
        catch { }
    }

    public void SaveSetting(string key, object value)
    {
        switch (key)
        {
            case "always_on_top":
                _settings.AlwaysOnTop = (bool)value;
                break;
            case "layout_mode":
                _settings.LayoutMode = (string)value;
                break;
            case "refresh_interval_ms":
                _settings.RefreshIntervalMs = NormalizeRefreshIntervalMs((int)value);
                break;
            case "status_detail_mode":
                _settings.StatusDetailMode = NormalizeStatusDetailMode((string)value);
                break;
        }
        Save();
    }

    public void SaveWindowGeometry(string layout, string geometry)
    {
        _settings.WindowGeometries[layout] = geometry;
        Save();
    }

    public ProcessLabel? GetLabel(string directory)
    {
        string key = NormalizeDirectoryKey(directory);
        return string.IsNullOrEmpty(key) ? null
            : _settings.ProcessLabels.GetValueOrDefault(key);
    }

    public void SetLabel(string directory, string name, string color)
    {
        string key = NormalizeDirectoryKey(directory);
        if (string.IsNullOrEmpty(key)) return;
        _settings.ProcessLabels[key] = new ProcessLabel { Name = name.Trim(), Color = color.Trim() };
        Save();
    }

    public void RemoveLabel(string directory)
    {
        string key = NormalizeDirectoryKey(directory);
        if (string.IsNullOrEmpty(key)) return;
        if (_settings.ProcessLabels.Remove(key))
            Save();
    }

    public static string NormalizeDirectoryKey(string directory) => directory.Trim();

    public static bool IsValidGeometry(string geometry) =>
        GeometryRegex().IsMatch(geometry);

    public static (int width, int height, int x, int y) ParseGeometry(string geometry)
    {
        var match = GeometryParseRegex().Match(geometry);
        if (!match.Success) return (0, 0, 0, 0);
        return (
            int.Parse(match.Groups[1].Value),
            int.Parse(match.Groups[2].Value),
            int.Parse(match.Groups[3].Value),
            int.Parse(match.Groups[4].Value)
        );
    }

    public static string FormatGeometry(int width, int height, int x, int y) =>
        $"{width}x{height}{x:+0;-0}{y:+0;-0}";

    private AppSettings Load()
    {
        try
        {
            if (File.Exists(_settingsPath))
            {
                string json = File.ReadAllText(_settingsPath);
                var settings = JsonSerializer.Deserialize<AppSettings>(json);
                if (settings != null)
                    return Normalize(settings);
            }
        }
        catch { }

        var defaults = AppSettings.CreateDefault();
        _settings = defaults;
        Save();
        return defaults;
    }

    private static AppSettings Normalize(AppSettings settings)
    {
        var defaults = AppSettings.CreateDefault();

        if (string.IsNullOrWhiteSpace(settings.LayoutMode) ||
            (settings.LayoutMode != "landscape" && settings.LayoutMode != "portrait"))
            settings.LayoutMode = defaults.LayoutMode;

        settings.RefreshIntervalMs = NormalizeRefreshIntervalMs(settings.RefreshIntervalMs);
        settings.StatusDetailMode = NormalizeStatusDetailMode(settings.StatusDetailMode);
        settings.HeadlessWslGraceSeconds = NormalizeHeadlessWslGraceSeconds(settings.HeadlessWslGraceSeconds);

        settings.WindowGeometries ??= new();
        foreach (var kvp in defaults.WindowGeometries)
        {
            if (!settings.WindowGeometries.ContainsKey(kvp.Key) ||
                !IsValidGeometry(settings.WindowGeometries[kvp.Key]))
                settings.WindowGeometries[kvp.Key] = kvp.Value;
        }

        settings.ProcessLabels ??= new();
        settings.WslTerminalAssignments ??= new();
        return settings;
    }

    private static int NormalizeRefreshIntervalMs(int refreshIntervalMs) =>
        Array.IndexOf(Constants.RefreshIntervalOptionsMs, refreshIntervalMs) >= 0
            ? refreshIntervalMs
            : Constants.DefaultRefreshIntervalMs;

    private static int NormalizeHeadlessWslGraceSeconds(int seconds) =>
        Math.Clamp(seconds, 0, 300);

    public static bool UsesScanDurationStatusDetailMode(string? statusDetailMode) =>
        string.Equals(statusDetailMode, Constants.StatusDetailModeScanDuration, StringComparison.OrdinalIgnoreCase) ||
        string.Equals(statusDetailMode, "scan_duration_ms", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(statusDetailMode, "scan_time", StringComparison.OrdinalIgnoreCase);

    public static bool UsesRefreshIntervalStatusDetailMode(string? statusDetailMode) =>
        string.Equals(statusDetailMode, Constants.StatusDetailModeRefreshInterval, StringComparison.OrdinalIgnoreCase) ||
        string.Equals(statusDetailMode, "refresh_interval_ms", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(statusDetailMode, "auto_refresh", StringComparison.OrdinalIgnoreCase);

    private static string NormalizeStatusDetailMode(string? statusDetailMode)
    {
        string normalized = statusDetailMode?.Trim().ToLowerInvariant() ?? "";
        if (UsesScanDurationStatusDetailMode(normalized) || UsesRefreshIntervalStatusDetailMode(normalized))
            return normalized;

        return Constants.StatusDetailModeRefreshInterval;
    }

    [GeneratedRegex(@"^\d+x\d+[+-]\d+[+-]\d+$")]
    private static partial Regex GeometryRegex();

    [GeneratedRegex(@"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")]
    private static partial Regex GeometryParseRegex();
}
