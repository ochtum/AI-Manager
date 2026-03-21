using System.Globalization;
using System.Windows.Media;

namespace AI_CLI_Watcher.Helpers;

public static class ColorHelper
{
    public static Color ParseColor(string colorText)
    {
        string color = colorText.Trim();
        if (string.IsNullOrEmpty(color))
            throw new FormatException("Enter a color.");

        string lowered = color.ToLowerInvariant();
        if (lowered.StartsWith('#'))
            return ParseHexColor(color);
        if (lowered.StartsWith("rgb(") && lowered.EndsWith(')'))
            return ParseRgbColor(color);
        if (lowered.StartsWith("okclh(") && lowered.EndsWith(')'))
            return ParseOklchColor(color);
        throw new FormatException("Use #hex, rgb(), or okclh().");
    }

    public static string ColorToHex(Color c) => $"#{c.R:x2}{c.G:x2}{c.B:x2}";

    public static Color HexToColor(string hex)
    {
        string h = hex.TrimStart('#');
        if (h.Length == 3)
            h = string.Concat(h[0], h[0], h[1], h[1], h[2], h[2]);
        if (h.Length >= 6)
        {
            byte r = byte.Parse(h[..2], NumberStyles.HexNumber);
            byte g = byte.Parse(h[2..4], NumberStyles.HexNumber);
            byte b = byte.Parse(h[4..6], NumberStyles.HexNumber);
            return Color.FromRgb(r, g, b);
        }
        throw new FormatException("Invalid hex color.");
    }

    public static Color TextColorForBackground(string hexColor)
    {
        Color c = HexToColor(hexColor);
        double luma = 0.299 * c.R + 0.587 * c.G + 0.114 * c.B;
        return luma > 170 ? HexToColor("#11111b") : HexToColor("#f9fafb");
    }

    public static SolidColorBrush BrushFromHex(string hex) => new(HexToColor(hex));

    private static Color ParseHexColor(string colorText)
    {
        string hex = colorText.TrimStart('#');
        if (hex.Length is 3 or 4)
            hex = string.Concat(hex[0], hex[0], hex[1], hex[1], hex[2], hex[2]);
        else if (hex.Length is 6 or 8)
            hex = hex[..6];
        else
            throw new FormatException("Hex colors must use #RGB or #RRGGBB.");

        if (!System.Text.RegularExpressions.Regex.IsMatch(hex, "^[0-9a-fA-F]{6}$"))
            throw new FormatException("Hex colors must only contain 0-9 or A-F.");

        return HexToColor(hex);
    }

    private static Color ParseRgbColor(string colorText)
    {
        string body = colorText[(colorText.IndexOf('(') + 1)..colorText.LastIndexOf(')')];
        body = body.Split('/')[0].Replace(",", " ");
        string[] parts = body.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 3)
            throw new FormatException("rgb() must include three components.");

        byte r = ParseRgbComponent(parts[0]);
        byte g = ParseRgbComponent(parts[1]);
        byte b = ParseRgbComponent(parts[2]);
        return Color.FromRgb(r, g, b);
    }

    private static byte ParseRgbComponent(string component)
    {
        string value = component.Trim();
        if (string.IsNullOrEmpty(value))
            throw new FormatException("RGB colors must include three components.");
        if (value.EndsWith('%'))
        {
            double percent = double.Parse(value[..^1], CultureInfo.InvariantCulture);
            return (byte)Math.Clamp((int)Math.Round(percent * 255 / 100), 0, 255);
        }
        double number = double.Parse(value, CultureInfo.InvariantCulture);
        if (number < 0 || number > 255)
            throw new FormatException("RGB components must be between 0 and 255.");
        return (byte)Math.Round(number);
    }

    private static Color ParseOklchColor(string colorText)
    {
        string body = colorText[(colorText.IndexOf('(') + 1)..colorText.LastIndexOf(')')];
        body = body.Split('/')[0].Replace(",", " ");
        string[] parts = body.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 3)
            throw new FormatException("oklch() must include lightness, chroma, and hue.");

        string lightRaw = parts[0].ToLowerInvariant();
        double lightness = lightRaw.EndsWith('%')
            ? double.Parse(lightRaw[..^1], CultureInfo.InvariantCulture) / 100
            : double.Parse(lightRaw, CultureInfo.InvariantCulture);
        if (lightness > 1 && lightness <= 100) lightness /= 100;
        if (lightness < 0 || lightness > 1)
            throw new FormatException("oklch lightness must be between 0 and 1.");

        string chromaRaw = parts[1].ToLowerInvariant();
        double chroma = chromaRaw.EndsWith('%')
            ? double.Parse(chromaRaw[..^1], CultureInfo.InvariantCulture) / 100
            : double.Parse(chromaRaw, CultureInfo.InvariantCulture);
        if (chroma < 0)
            throw new FormatException("oklch chroma must be 0 or greater.");

        double hue = ParseAngle(parts[2]) % 360;
        return OklchToColor(lightness, chroma, hue);
    }

    private static double ParseAngle(string value)
    {
        string angle = value.Trim().ToLowerInvariant();
        if (angle.EndsWith("deg")) return double.Parse(angle[..^3], CultureInfo.InvariantCulture);
        if (angle.EndsWith("grad")) return double.Parse(angle[..^4], CultureInfo.InvariantCulture) * 0.9;
        if (angle.EndsWith("rad")) return double.Parse(angle[..^3], CultureInfo.InvariantCulture) * (180.0 / Math.PI);
        if (angle.EndsWith("turn")) return double.Parse(angle[..^4], CultureInfo.InvariantCulture) * 360.0;
        return double.Parse(angle, CultureInfo.InvariantCulture);
    }

    private static double LinearToSrgb(double value)
    {
        value = Math.Clamp(value, 0, 1);
        return value <= 0.0031308 ? 12.92 * value : 1.055 * Math.Pow(value, 1.0 / 2.4) - 0.055;
    }

    private static Color OklchToColor(double lightness, double chroma, double hue)
    {
        double hueRad = hue * Math.PI / 180;
        double a = chroma * Math.Cos(hueRad);
        double b = chroma * Math.Sin(hueRad);

        double l_ = lightness + 0.3963377774 * a + 0.2158037573 * b;
        double m_ = lightness - 0.1055613458 * a - 0.0638541728 * b;
        double s_ = lightness - 0.0894841775 * a - 1.2914855480 * b;

        double l = l_ * l_ * l_;
        double m = m_ * m_ * m_;
        double s = s_ * s_ * s_;

        double redLinear = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
        double greenLinear = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
        double blueLinear = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;

        byte r = (byte)Math.Clamp((int)Math.Round(LinearToSrgb(redLinear) * 255), 0, 255);
        byte g = (byte)Math.Clamp((int)Math.Round(LinearToSrgb(greenLinear) * 255), 0, 255);
        byte bVal = (byte)Math.Clamp((int)Math.Round(LinearToSrgb(blueLinear) * 255), 0, 255);
        return Color.FromRgb(r, g, bVal);
    }
}
