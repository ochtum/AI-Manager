namespace AI_CLI_Watcher.Models;

public record CliDefinition(
    string DisplayName,
    string[] ExePatterns,
    string[] CmdlineKeywords,
    string[] CmdlineExclude,
    string[] PathKeywords
);

public record WslCliDefinition(
    string DisplayName,
    string[] ExePatterns,
    string[] CmdlineKeywords,
    string[] CmdlineExclude
);

public static class CliDefinitions
{
    public static readonly CliDefinition[] Windows =
    [
        new("Claude Code",
            ["claude.exe", "claude"],
            ["@anthropic-ai/claude-code", "claude-code"],
            [],
            []),
        new("Codex CLI",
            ["codex.exe", "codex"],
            ["@openai/codex"],
            ["app-server"],
            ["@openai", "codex"]),
        new("GitHub Copilot CLI",
            ["copilot.exe", "copilot", "github-copilot-cli.exe", "github-copilot-cli"],
            ["@github/copilot", "@githubnext/github-copilot-cli", "github-copilot-cli"],
            ["microsoft.copilot", "m365copilot"],
            ["@github", "npm"]),
    ];

    public static readonly WslCliDefinition[] Wsl =
    [
        new("Claude Code",
            ["claude"],
            ["@anthropic-ai/claude-code", "/bin/claude", "/.claude/local/claude"],
            []),
        new("Codex CLI",
            ["codex"],
            ["@openai/codex", "/bin/codex", "codex/codex"],
            ["app-server"]),
        new("GitHub Copilot CLI",
            ["copilot", "github-copilot-cli"],
            ["@github/copilot", "@githubnext/github-copilot-cli", "github-copilot-cli", "/bin/copilot"],
            ["microsoft.copilot", "m365copilot"]),
    ];

    public static readonly HashSet<string> WindowsWrapperExes = new(StringComparer.OrdinalIgnoreCase)
    {
        "node.exe", "node", "npm.exe", "npm", "npx.exe", "npx",
        "pnpm.exe", "pnpm", "bun.exe", "bun"
    };

    public static readonly HashSet<string> WslLauncherExes = new(StringComparer.OrdinalIgnoreCase)
    {
        "node", "npm", "npx", "pnpm", "bun", "bash", "sh", "env"
    };

    public static readonly HashSet<string> WslTabTtyProcessNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "bash", "zsh", "fish", "sh", "tmux", "screen", "nu", "nushell", "pwsh", "powershell"
    };

    public static readonly string[] NonInteractivePatterns = [" mcp-server", " --mcp-server"];

    public static readonly Dictionary<string, string> TerminalLabels = new(StringComparer.OrdinalIgnoreCase)
    {
        ["windowsterminal.exe"] = "Windows Terminal",
        ["wt.exe"] = "Windows Terminal",
        ["cmd.exe"] = "Command Prompt",
        ["powershell.exe"] = "PowerShell",
        ["pwsh.exe"] = "PowerShell",
        ["mintty.exe"] = "MinTTY",
        ["alacritty.exe"] = "Alacritty",
        ["wezterm-gui.exe"] = "WezTerm",
        ["conhost.exe"] = "Console",
    };
}
