using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using AI_CLI_Watcher.Helpers;
using AI_CLI_Watcher.Models;
using AI_CLI_Watcher.Services;
using AI_CLI_Watcher.Views;

namespace AI_CLI_Watcher;

public partial class MainWindow : Window
{
    private readonly SettingsService _settingsService;
    private readonly ProcessScanner _processScanner = new();
    private readonly WslScanner _wslScanner = new();
    private readonly DispatcherTimer _refreshTimer;
    private readonly ObservableCollection<ProcessViewModel> _processViewModels = [];

    private string _currentLayout = "landscape";
    private string _windowMode = "normal";
    private string? _restoreLayoutAfterMinimize;
    private bool _isScanning;
    private bool _suspendGeometryTracking;
    private DispatcherTimer? _geometrySaveTimer;
    private Dictionary<int, CliProcess> _processLookup = new();

    public MainWindow()
    {
        string settingsPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "settings.json");
        _settingsService = new SettingsService(settingsPath);

        InitializeComponent();

        ProcessDataGrid.ItemsSource = _processViewModels;
        CardsItemsControl.ItemsSource = _processViewModels;

        // Wire up console HWND resolver on UI thread for both scanners
        // (FreeConsole/AttachConsole modifies process-global state, must run on UI thread)
        Func<uint, nint?> consoleHwndResolver = pid =>
            Dispatcher.Invoke(() => Win32Api.GetConsoleHwndForPid(pid));
        _wslScanner.ConsoleHwndResolver = consoleHwndResolver;
        _processScanner.ConsoleHwndResolver = consoleHwndResolver;

        // Restore settings
        TopMostCheckBox.IsChecked = _settingsService.Settings.AlwaysOnTop;
        Topmost = _settingsService.Settings.AlwaysOnTop;

        _currentLayout = _settingsService.Settings.LayoutMode;
        ApplyLayout(initial: true);

        _refreshTimer = new DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(Constants.RefreshIntervalMs)
        };
        _refreshTimer.Tick += (_, _) => DoRefresh();
        _refreshTimer.Start();

        Loaded += (_, _) => DoRefresh();

        PrintStartupBanner();
    }

    private static void PrintStartupBanner()
    {
        Console.WriteLine(new string('=', 50));
        Console.WriteLine("  AI CLI Watcher - AI CLI Process Monitor");
        Console.WriteLine(new string('=', 50));
        Console.WriteLine();
        Console.WriteLine($"  Started at : {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        Console.WriteLine($"  Refresh    : {Constants.RefreshIntervalMs}ms");
        Console.WriteLine();
        Console.WriteLine("  Monitoring: Claude Code / Codex CLI / GitHub Copilot CLI");
        Console.WriteLine();
        Console.WriteLine("  Close the GUI window to exit.");
        Console.WriteLine(new string('-', 50));
    }

    // ---- Refresh ----

    private async void DoRefresh()
    {
        if (_isScanning || _windowMode == "minimized") return;
        _isScanning = true;

        try
        {
            var procs = await Task.Run(() =>
            {
                var winProcs = _processScanner.ScanWindowsProcesses();
                try
                {
                    var wslProcs = _wslScanner.ScanWslProcesses();
                    winProcs.AddRange(wslProcs);
                }
                catch { }
                return winProcs;
            });

            UpdateViews(procs);
        }
        catch { }
        finally
        {
            _isScanning = false;
        }
    }

    private void UpdateViews(List<CliProcess> procs)
    {
        _processLookup = procs.ToDictionary(p => p.Pid);

        // Sync view models
        var existingByPid = _processViewModels.ToDictionary(vm => vm.Pid);
        var newPids = procs.Select(p => p.Pid).ToHashSet();

        // Remove stale
        for (int i = _processViewModels.Count - 1; i >= 0; i--)
        {
            if (!newPids.Contains(_processViewModels[i].Pid))
                _processViewModels.RemoveAt(i);
        }

        // Update existing or add new
        for (int i = 0; i < procs.Count; i++)
        {
            var p = procs[i];
            if (existingByPid.TryGetValue(p.Pid, out var existing))
            {
                existing.UpdateFrom(p, _settingsService);
                // Reorder if needed
                int currentIndex = _processViewModels.IndexOf(existing);
                if (currentIndex != i && currentIndex >= 0)
                    _processViewModels.Move(currentIndex, i);
            }
            else
            {
                var vm = new ProcessViewModel(p, _settingsService);
                if (i < _processViewModels.Count)
                    _processViewModels.Insert(i, vm);
                else
                    _processViewModels.Add(vm);
            }
        }

        // DataGrid row background by status
        UpdateDataGridRowColors();

        string ts = DateTime.Now.ToString("HH:mm:ss");
        StatusLabel.Text = $"{procs.Count} found | {ts} | Auto refresh: {Constants.RefreshIntervalMs / 1000}s";
    }

    private void UpdateDataGridRowColors()
    {
        Dispatcher.BeginInvoke(DispatcherPriority.Loaded, () =>
        {
            foreach (var vm in _processViewModels)
            {
                var row = ProcessDataGrid.ItemContainerGenerator.ContainerFromItem(vm) as DataGridRow;
                if (row != null)
                {
                    row.Background = vm.IsProcessing
                        ? ColorHelper.BrushFromHex("#3a1a1a")
                        : ColorHelper.BrushFromHex("#1a3a2a");
                }
            }
        });
    }

    // ---- Layout ----

    private void ApplyLayout(bool initial = false)
    {
        _suspendGeometryTracking = true;

        if (_currentLayout == "portrait")
        {
            MinWidth = Constants.PortraitMinWidth;
            MinHeight = Constants.PortraitMinHeight;
            ProcessDataGrid.Visibility = Visibility.Collapsed;
            CardsScrollViewer.Visibility = Visibility.Visible;
            LayoutButton.Content = "Table";
            HeaderBorder.Padding = new Thickness(8, 6, 8, 6);
            ContentGrid.Margin = new Thickness(6);

            // Portrait layout: title on row 0, controls on row 1
            Grid.SetColumnSpan(ControlsPanel, 3);
            Grid.SetRow(ControlsPanel, 1);
            Grid.SetColumn(ControlsPanel, 0);
            ControlsPanel.Margin = new Thickness(0, 4, 0, 0);

            Grid.SetRow(StatusLabel, 0);
            Grid.SetColumn(StatusLabel, 1);
            StatusLabel.Margin = new Thickness(8, 0, 0, 0);
        }
        else
        {
            MinWidth = Constants.LandscapeMinWidth;
            MinHeight = Constants.LandscapeMinHeight;
            ProcessDataGrid.Visibility = Visibility.Visible;
            CardsScrollViewer.Visibility = Visibility.Collapsed;
            LayoutButton.Content = "Cards";
            HeaderBorder.Padding = new Thickness(16, 10, 16, 10);
            ContentGrid.Margin = new Thickness(8);

            // Landscape layout: all on row 0
            Grid.SetColumnSpan(ControlsPanel, 1);
            Grid.SetRow(ControlsPanel, 0);
            Grid.SetColumn(ControlsPanel, 2);
            ControlsPanel.Margin = new Thickness(0);

            Grid.SetRow(StatusLabel, 0);
            Grid.SetColumn(StatusLabel, 3);
            StatusLabel.Margin = new Thickness(16, 0, 0, 0);
        }

        RestoreWindowGeometry(_currentLayout, preservePosition: !initial);
        _suspendGeometryTracking = false;
        ScheduleGeometrySave();
    }

    private void LayoutButton_Click(object sender, RoutedEventArgs e)
    {
        SaveWindowGeometry(_currentLayout);
        _currentLayout = _currentLayout == "landscape" ? "portrait" : "landscape";
        _settingsService.SaveSetting("layout_mode", _currentLayout);
        ApplyLayout();
    }

    private void MinimizeButton_Click(object sender, RoutedEventArgs e)
    {
        if (_windowMode == "minimized") return;
        SaveWindowGeometry(_currentLayout);
        _restoreLayoutAfterMinimize = _currentLayout;
        _windowMode = "minimized";

        _suspendGeometryTracking = true;
        MinWidth = Constants.MinimizedMinWidth;
        MinHeight = Constants.MinimizedMinHeight;

        // Hide normal content, show minimized
        HeaderBorder.Visibility = Visibility.Collapsed;
        ContentGrid.Visibility = Visibility.Collapsed;
        HintLabel.Visibility = Visibility.Collapsed;
        MinimizedPanel.Visibility = Visibility.Visible;

        // Restore saved position but always use the fixed minimized size
        var geom = _settingsService.Settings.WindowGeometries.GetValueOrDefault("minimized");
        if (geom != null && SettingsService.IsValidGeometry(geom))
        {
            var (_, _, x, y) = SettingsService.ParseGeometry(geom);
            Left = x;
            Top = y;
        }
        Width = Constants.MinimizedMinWidth;
        Height = Constants.MinimizedMinHeight;

        _suspendGeometryTracking = false;
        ScheduleGeometrySave();
    }

    private void RestoreButton_Click(object sender, RoutedEventArgs e)
    {
        if (_windowMode != "minimized") return;
        SaveWindowGeometry("minimized");

        _windowMode = "normal";
        string restoreLayout = _restoreLayoutAfterMinimize ?? _currentLayout;
        _restoreLayoutAfterMinimize = null;
        _currentLayout = restoreLayout;

        // Restore normal content
        HeaderBorder.Visibility = Visibility.Visible;
        ContentGrid.Visibility = Visibility.Visible;
        HintLabel.Visibility = Visibility.Visible;
        MinimizedPanel.Visibility = Visibility.Collapsed;

        ApplyLayout();
        DoRefresh();
    }

    // ---- Always on Top ----

    private void TopMostCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        bool isChecked = TopMostCheckBox.IsChecked == true;
        Topmost = isChecked;
        _settingsService.SaveSetting("always_on_top", isChecked);
    }

    // ---- Window Geometry ----

    private void RestoreWindowGeometry(string layout, bool preservePosition = false)
    {
        var geom = _settingsService.Settings.WindowGeometries.GetValueOrDefault(layout);
        if (geom == null || !SettingsService.IsValidGeometry(geom))
        {
            geom = layout switch
            {
                "landscape" => Constants.LandscapeGeometry,
                "portrait" => Constants.PortraitGeometry,
                "minimized" => Constants.MinimizedGeometry,
                _ => Constants.LandscapeGeometry,
            };
        }

        var (w, h, x, y) = SettingsService.ParseGeometry(geom);
        if (preservePosition)
        {
            x = (int)Left;
            y = (int)Top;
        }
        Width = w;
        Height = h;
        Left = x;
        Top = y;
    }

    private void SaveWindowGeometry(string? layout = null)
    {
        string targetLayout = layout ?? (_windowMode == "minimized" ? "minimized" : _currentLayout);
        if (WindowState != WindowState.Normal) return;
        string geom = SettingsService.FormatGeometry(
            (int)ActualWidth, (int)ActualHeight, (int)Left, (int)Top);
        _settingsService.SaveWindowGeometry(targetLayout, geom);
    }

    private void ScheduleGeometrySave()
    {
        if (_suspendGeometryTracking || WindowState != WindowState.Normal) return;
        _geometrySaveTimer?.Stop();
        _geometrySaveTimer = new DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(Constants.GeometrySaveDelayMs)
        };
        _geometrySaveTimer.Tick += (_, _) =>
        {
            _geometrySaveTimer?.Stop();
            SaveWindowGeometry();
        };
        _geometrySaveTimer.Start();
    }

    private void Window_LocationChanged(object? sender, EventArgs e) => ScheduleGeometrySave();
    private void Window_SizeChanged(object sender, SizeChangedEventArgs e) => ScheduleGeometrySave();

    private void Window_Closing(object? sender, CancelEventArgs e)
    {
        _geometrySaveTimer?.Stop();
        SaveWindowGeometry();
        _refreshTimer.Stop();
    }

    // ---- Process Activation ----

    private void ActivatePid(int pid)
    {
        if (!_processLookup.TryGetValue(pid, out var proc) || proc.Hwnds.Count == 0)
        {
            StatusLabel.Text = $"No window found for PID {pid}";
            return;
        }

        try
        {
            nint hwnd = proc.Hwnds[0];
            StatusLabel.Text = $"Activating {proc.Name} (PID {pid})";
            Win32Api.ActivateWindow(hwnd);
        }
        catch (Exception ex)
        {
            StatusLabel.Text = $"Failed to activate: {ex.Message}";
        }
    }

    private void DataGrid_MouseDoubleClick(object sender, MouseButtonEventArgs e)
    {
        if (ProcessDataGrid.SelectedItem is ProcessViewModel vm)
            ActivatePid(vm.Pid);
    }

    private void DataGrid_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && ProcessDataGrid.SelectedItem is ProcessViewModel vm)
        {
            ActivatePid(vm.Pid);
            e.Handled = true;
        }
    }

    private void Card_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ClickCount == 2 && sender is FrameworkElement fe && fe.DataContext is ProcessViewModel vm)
            ActivatePid(vm.Pid);
    }

    // ---- Label Management ----

    private void LabelButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int pid) return;
        OpenLabelEditor(pid);
    }

    private void LabelDeleteButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int pid) return;
        if (!_processLookup.TryGetValue(pid, out var proc)) return;
        string dir = SettingsService.NormalizeDirectoryKey(proc.Cwd);
        if (string.IsNullOrEmpty(dir)) return;
        _settingsService.RemoveLabel(dir);
        RefreshAllLabels();
    }

    private void OpenLabelEditor(int pid)
    {
        if (!_processLookup.TryGetValue(pid, out var proc)) return;
        string dir = SettingsService.NormalizeDirectoryKey(proc.Cwd);
        if (string.IsNullOrEmpty(dir))
        {
            MessageBox.Show(
                "This process does not have a directory yet, so the label cannot be saved.",
                "Process Label", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }

        var existing = _settingsService.GetLabel(dir);
        var dialog = new LabelEditorDialog(dir, existing?.Name, existing?.Color)
        {
            Owner = this,
        };

        if (dialog.ShowDialog() == true)
        {
            if (dialog.Deleted)
                _settingsService.RemoveLabel(dir);
            else if (dialog.ResultName != null && dialog.ResultColor != null)
                _settingsService.SetLabel(dir, dialog.ResultName, dialog.ResultColor);
            RefreshAllLabels();
        }
    }

    private void RefreshAllLabels()
    {
        foreach (var vm in _processViewModels)
        {
            if (_processLookup.TryGetValue(vm.Pid, out var proc))
                vm.UpdateLabelFrom(proc, _settingsService);
        }
        UpdateDataGridRowColors();
    }

    // ---- Helpers ----

    private static string CompactDirectory(string directory, int maxChars)
    {
        string normalized = directory.Trim();
        if (string.IsNullOrEmpty(normalized)) return "(unknown)";
        if (normalized.Length <= maxChars) return normalized;

        string separator = normalized.Contains('\\') && !normalized.Contains('/') ? "\\" : "/";
        string[] parts = Regex.Split(normalized, @"[\\/]+").Where(p => !string.IsNullOrEmpty(p)).ToArray();
        if (parts.Length == 0) return TruncateFromLeft(normalized, maxChars);

        string tail = parts[^1];
        if (tail.Length + 4 > maxChars) return TruncateFromLeft(tail, maxChars);

        for (int i = parts.Length - 2; i >= 0; i--)
        {
            string candidate = $"{parts[i]}{separator}{tail}";
            string display = $"...{separator}{candidate}";
            if (display.Length > maxChars) break;
            tail = candidate;
        }
        return $"...{separator}{tail}";
    }

    private static string TruncateFromLeft(string text, int maxChars)
    {
        if (maxChars <= 0) return "";
        if (text.Length <= maxChars) return text;
        if (maxChars <= 3) return text[^maxChars..];
        return "..." + text[^(maxChars - 3)..];
    }

    // ---- Process View Model ----

    public class ProcessViewModel : INotifyPropertyChanged
    {
        private static readonly Dictionary<string, (string icon, string accent, string headerBg)> CliVisuals = new()
        {
            ["Claude Code"] = ("●", "#89b4fa", "#25344f"),
            ["Codex CLI"] = ("◆", "#f9e2af", "#4b4030"),
            ["GitHub Copilot CLI"] = ("▲", "#a6e3a1", "#2a4131"),
        };

        public int Pid { get; private set; }
        public string DisplayName { get; private set; } = "";
        public string StatusText { get; private set; } = "";
        public string CpuText { get; private set; } = "";
        public string CpuDisplayText { get; private set; } = "";
        public string DirectoryDisplay { get; private set; } = "";
        public string DirectoryCardDisplay { get; private set; } = "";
        public string TerminalType { get; private set; } = "";
        public bool IsProcessing { get; private set; }

        public SolidColorBrush CliAccentBrush { get; private set; } = Brushes.White;
        public SolidColorBrush StatusBgBrush { get; private set; } = Brushes.Transparent;
        public SolidColorBrush StatusAccentBrush { get; private set; } = Brushes.White;
        public SolidColorBrush StatusBadgeBgBrush { get; private set; } = Brushes.Gray;

        // Label properties
        public string LabelDisplayText { get; private set; } = "+ Label";
        public SolidColorBrush LabelBgBrush { get; private set; } = ColorHelper.BrushFromHex("#25283d");
        public SolidColorBrush LabelFgBrush { get; private set; } = ColorHelper.BrushFromHex("#a6adc8");
        public FontWeight LabelFontWeight { get; private set; } = FontWeights.Normal;
        public Thickness LabelBorderThickness { get; private set; } = new(1);
        public Visibility LabelDeleteVisibility { get; private set; } = Visibility.Collapsed;

        public ProcessViewModel(CliProcess p, SettingsService settings)
        {
            UpdateFrom(p, settings);
        }

        public void UpdateFrom(CliProcess p, SettingsService settings)
        {
            Pid = p.Pid;
            IsProcessing = p.IsProcessing;

            var (icon, accent, _) = GetCliVisual(p.Name);
            DisplayName = $"{icon} {p.Name}";
            CliAccentBrush = ColorHelper.BrushFromHex(accent);

            StatusText = p.Status;
            CpuText = $"{p.CpuPercent:F1}";
            CpuDisplayText = $"{p.CpuPercent:F1}%";
            DirectoryDisplay = CompactDirectory(p.Cwd, 34);
            DirectoryCardDisplay = CompactDirectory(p.Cwd, 42);
            TerminalType = string.IsNullOrEmpty(p.TerminalType) ? "(unknown)" : p.TerminalType;

            if (p.IsProcessing)
            {
                StatusBgBrush = ColorHelper.BrushFromHex("#3a1a1a");
                StatusAccentBrush = ColorHelper.BrushFromHex("#f38ba8");
                StatusBadgeBgBrush = ColorHelper.BrushFromHex("#6c2742");
            }
            else
            {
                StatusBgBrush = ColorHelper.BrushFromHex("#1a3a2a");
                StatusAccentBrush = ColorHelper.BrushFromHex("#a6e3a1");
                StatusBadgeBgBrush = ColorHelper.BrushFromHex("#2f6549");
            }

            UpdateLabelFrom(p, settings);
            OnPropertyChanged(null);
        }

        public void UpdateLabelFrom(CliProcess p, SettingsService settings)
        {
            var label = settings.GetLabel(p.Cwd);
            if (label != null)
            {
                try
                {
                    var color = ColorHelper.ParseColor(label.Color);
                    string hex = ColorHelper.ColorToHex(color);
                    var textColor = ColorHelper.TextColorForBackground(hex);
                    LabelDisplayText = label.Name;
                    LabelBgBrush = new SolidColorBrush(color);
                    LabelFgBrush = new SolidColorBrush(textColor);
                    LabelFontWeight = FontWeights.Bold;
                    LabelBorderThickness = new Thickness(0);
                    LabelDeleteVisibility = Visibility.Visible;
                }
                catch
                {
                    SetUnlabeledStyle(p.Cwd);
                }
            }
            else
            {
                SetUnlabeledStyle(p.Cwd);
            }
            OnPropertyChanged(nameof(LabelDisplayText));
            OnPropertyChanged(nameof(LabelBgBrush));
            OnPropertyChanged(nameof(LabelFgBrush));
            OnPropertyChanged(nameof(LabelFontWeight));
            OnPropertyChanged(nameof(LabelBorderThickness));
            OnPropertyChanged(nameof(LabelDeleteVisibility));
        }

        private void SetUnlabeledStyle(string cwd)
        {
            string dir = SettingsService.NormalizeDirectoryKey(cwd);
            if (string.IsNullOrEmpty(dir))
            {
                LabelDisplayText = "No Label";
                LabelBgBrush = ColorHelper.BrushFromHex("#181825");
                LabelFgBrush = ColorHelper.BrushFromHex("#6c7086");
            }
            else
            {
                LabelDisplayText = "+ Label";
                LabelBgBrush = ColorHelper.BrushFromHex("#25283d");
                LabelFgBrush = ColorHelper.BrushFromHex("#a6adc8");
            }
            LabelFontWeight = FontWeights.Normal;
            LabelBorderThickness = new Thickness(1);
            LabelDeleteVisibility = Visibility.Collapsed;
        }

        private static (string icon, string accent, string headerBg) GetCliVisual(string cliName)
        {
            foreach (var (baseName, visual) in CliVisuals)
            {
                if (cliName == baseName || cliName.StartsWith($"{baseName} "))
                    return visual;
            }
            return ("■", "#94e2d5", "#2b3d3d");
        }

        public event PropertyChangedEventHandler? PropertyChanged;

        private void OnPropertyChanged(string? propertyName)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        }
    }
}