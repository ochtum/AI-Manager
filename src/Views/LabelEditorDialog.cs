using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using AI_CLI_Watcher.Helpers;

namespace AI_CLI_Watcher.Views;

public partial class LabelEditorDialog : Window
{
    private readonly (string name, string color)[] _presets =
    [
        ("Red", "#f38ba8"),
        ("Blue", "#89b4fa"),
        ("Green", "#a6e3a1"),
        ("Yellow", "#f9e2af"),
        ("Purple", "#cba6f7"),
    ];

    public string? ResultName { get; private set; }
    public string? ResultColor { get; private set; }
    public bool Deleted { get; private set; }

    public LabelEditorDialog(string directory, string? existingName, string? existingColor)
    {
        InitializeComponent();

        DirectoryText.Text = directory;
        NameTextBox.Text = existingName ?? "";
        ColorTextBox.Text = existingColor ?? _presets[0].color;

        if (existingName != null)
            DeleteButton.Visibility = Visibility.Visible;

        CreatePresetButtons();
        NameTextBox.TextChanged += (_, _) => UpdatePreview();
        ColorTextBox.TextChanged += (_, _) => UpdatePreview();
        UpdatePreview();

        NameTextBox.Focus();
    }

    private void CreatePresetButtons()
    {
        foreach (var (name, color) in _presets)
        {
            var btn = new Button
            {
                Content = name,
                FontSize = 10,
                FontWeight = FontWeights.Bold,
                FontFamily = new FontFamily("Segoe UI"),
                Padding = new Thickness(8, 3, 8, 3),
                Margin = new Thickness(0, 0, 6, 0),
                Cursor = System.Windows.Input.Cursors.Hand,
                BorderThickness = new Thickness(0),
            };
            var bgColor = ColorHelper.HexToColor(color);
            var fgColor = ColorHelper.TextColorForBackground(color);
            btn.Background = new SolidColorBrush(bgColor);
            btn.Foreground = new SolidColorBrush(fgColor);
            string capturedColor = color;
            btn.Click += (_, _) =>
            {
                ColorTextBox.Text = capturedColor;
                UpdatePreview();
            };
            PresetPanel.Children.Add(btn);
        }
    }

    private void UpdatePreview()
    {
        string previewName = string.IsNullOrWhiteSpace(NameTextBox.Text) ? "Preview" : NameTextBox.Text.Trim();
        try
        {
            var parsedColor = ColorHelper.ParseColor(ColorTextBox.Text);
            string hex = ColorHelper.ColorToHex(parsedColor);
            var fgColor = ColorHelper.TextColorForBackground(hex);
            PreviewBorder.Background = new SolidColorBrush(parsedColor);
            PreviewText.Text = previewName;
            PreviewText.Foreground = new SolidColorBrush(fgColor);
            PreviewHint.Text = "Use #hex, rgb(), or okclh().";
            PreviewHint.Foreground = ColorHelper.BrushFromHex("#a6adc8");
        }
        catch (Exception ex)
        {
            PreviewBorder.Background = ColorHelper.BrushFromHex("#24273a");
            PreviewText.Text = previewName;
            PreviewText.Foreground = ColorHelper.BrushFromHex("#cdd6f4");
            PreviewHint.Text = ex.Message;
            PreviewHint.Foreground = ColorHelper.BrushFromHex("#f38ba8");
        }
    }

    private void SaveButton_Click(object sender, RoutedEventArgs e)
    {
        string name = NameTextBox.Text.Trim();
        if (string.IsNullOrEmpty(name))
        {
            MessageBox.Show("Enter a label name.", "Process Label", MessageBoxButton.OK, MessageBoxImage.Error);
            NameTextBox.Focus();
            return;
        }
        try
        {
            ColorHelper.ParseColor(ColorTextBox.Text);
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Process Label", MessageBoxButton.OK, MessageBoxImage.Error);
            ColorTextBox.Focus();
            return;
        }

        ResultName = name;
        ResultColor = ColorTextBox.Text.Trim();
        DialogResult = true;
        Close();
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }

    private void DeleteButton_Click(object sender, RoutedEventArgs e)
    {
        Deleted = true;
        DialogResult = true;
        Close();
    }
}
