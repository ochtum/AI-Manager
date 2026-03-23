using System.Reflection;

namespace AI_CLI_Watcher.Helpers;

public static class ApplicationVersion
{
    public static string FooterText => $"Version {GetDisplayVersion()}";

    private static string GetDisplayVersion()
    {
        Assembly assembly = typeof(ApplicationVersion).Assembly;
        string? metadataVersion = assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(attribute => attribute.Key == "DisplayVersion")
            ?.Value;

        if (!string.IsNullOrWhiteSpace(metadataVersion))
        {
            return metadataVersion;
        }

        string? informationalVersion = assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
            .InformationalVersion;

        if (!string.IsNullOrWhiteSpace(informationalVersion))
        {
            int metadataSeparatorIndex = informationalVersion.IndexOf('+');
            return metadataSeparatorIndex >= 0
                ? informationalVersion[..metadataSeparatorIndex]
                : informationalVersion;
        }

        Version? assemblyVersion = assembly.GetName().Version;
        return assemblyVersion is null ? "unknown" : assemblyVersion.ToString(3);
    }
}
