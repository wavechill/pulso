using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal class WindowFrame : Form
{
    private const int NcCalcSize = 0x0083;
    private const int NcHitTest = 0x0084;
    private const int NcPaint = 0x0085;
    private const int NcActivate = 0x0086;
    private const int GetMinMaxInfo = 0x0024;
    private const int SizeMessage = 0x0005;
    private const int NormalSizeMode = 0;
    private const int ShowMaximized = 3;
    private const int ShowMinimized = 6;
    private const int ShowRestored = 9;
    private const int CornerPreferenceAttribute = 33;
    private const int BorderColorAttribute = 34;
    private const uint SquareCorners = 1;
    private const uint RoundedCorners = 2;
    private readonly CaptionButton minimize;
    private readonly CaptionButton maximize;
    private readonly CaptionButton close;
    private Rectangle restoreBounds;
    private FormWindowState restoreState;
    private int sizeMode;
    private Size normalSize;
    protected bool FullScreen { get; private set; }

    protected WindowFrame()
    {
        AutoScaleMode = AutoScaleMode.Dpi;
        minimize = new CaptionButton(CaptionAction.Minimize);
        maximize = new CaptionButton(CaptionAction.Maximize);
        close = new CaptionButton(CaptionAction.Close);
        minimize.Click += (sender, args) => ShowWindow(Handle, ShowMinimized);
        maximize.Click += (sender, args) =>
            ShowWindow(Handle, WindowState == FormWindowState.Maximized ? ShowRestored : ShowMaximized);
        close.Click += (sender, args) => Close();
        Controls.AddRange(new Control[] { minimize, maximize, close });
        RefreshFrame();
    }

    protected void FloatWindowButtons()
    {
        minimize.BringToFront();
        maximize.BringToFront();
        close.BringToFront();
    }

    protected override CreateParams CreateParams
    {
        get
        {
            var parameters = base.CreateParams;
            parameters.Style &= ~0x00c00000; // WS_CAPTION
            parameters.Style |= 0x000f0000; // resize, system menu, minimize and maximize
            return parameters;
        }
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        RefreshFrame();
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        const uint NoBorderColor = 0xfffffffe;
        uint color = NoBorderColor;
        DwmSetWindowAttribute(Handle, BorderColorAttribute, ref color, sizeof(uint));
        ApplyCornerPreference();
        RefreshFrame();
    }

    private void ApplyCornerPreference()
    {
        uint preference = FullScreen ? SquareCorners : RoundedCorners;
        DwmSetWindowAttribute(Handle, CornerPreferenceAttribute, ref preference, sizeof(uint));
    }

    private void RefreshFrame()
    {
        if (close == null) return;
        var padding = FullScreen || WindowState == FormWindowState.Maximized ? Padding.Empty : new Padding(6);
        if (Padding != padding) Padding = padding;
        minimize.Visible = maximize.Visible = close.Visible = !FullScreen;
        maximize.ShowRestore(WindowState == FormWindowState.Maximized);
        if (FullScreen) return;

        // Float the native buttons over the game's existing header.
        float scale = IsHandleCreated ? GetDpiForWindow(Handle) / 96f : 1f;
        int width = (int)Math.Round(36 * scale), height = (int)Math.Round(32 * scale);
        int gap = (int)Math.Round(2 * scale);
        int right = ClientSize.Width - padding.Right - (int)Math.Round(14 * scale);
        float headerWidth = (ClientSize.Width - padding.Horizontal) / scale;
        int top = padding.Top + (int)Math.Round((headerWidth > 650 ? 23 : 14) * scale);
        close.SetBounds(right - width, top, width, height);
        maximize.SetBounds(close.Left - gap - width, top, width, height);
        minimize.SetBounds(maximize.Left - gap - width, top, width, height);
    }

    protected void ToggleFullScreen(bool enabled)
    {
        if (enabled == FullScreen) return;
        if (enabled)
        {
            var screen = Screen.FromHandle(Handle);
            restoreState = WindowState;
            restoreBounds = restoreState == FormWindowState.Normal ? Bounds : RestoreBounds;
            if (restoreState == FormWindowState.Maximized && !normalSize.IsEmpty)
                restoreBounds.Size = normalSize;
            FullScreen = true;
            WindowState = FormWindowState.Normal;
            Bounds = screen.Bounds;
        }
        else
        {
            FullScreen = false;
            Bounds = restoreBounds;
            WindowState = restoreState;
        }
        ApplyCornerPreference();
        RefreshFrame();
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == SizeMessage)
        {
            int nextMode = message.WParam.ToInt32();
            bool restoring = sizeMode != NormalSizeMode && nextMode == NormalSizeMode;
            Size previous = normalSize;
            sizeMode = nextMode;
            base.WndProc(ref message);
            if (sizeMode == NormalSizeMode && !FullScreen)
            {
                // WinForms otherwise adds the traditional non-client border
                // to the saved size when restoring a window with WS_THICKFRAME.
                NativeRect frame;
                GetWindowRect(message.HWnd, out frame);
                var actual = new Size(frame.Right - frame.Left, frame.Bottom - frame.Top);
                if (restoring && !previous.IsEmpty)
                {
                    BeginInvoke((Action)(() =>
                    {
                        if (!FullScreen && sizeMode == NormalSizeMode)
                            SetWindowPos(Handle, IntPtr.Zero, 0, 0, previous.Width, previous.Height, 0x0016);
                    }));
                }
                normalSize = actual;
            }
            return;
        }
        if (message.Msg == NcCalcSize || message.Msg == NcPaint)
        {
            message.Result = IntPtr.Zero;
            return;
        }
        if (message.Msg == NcActivate)
        {
            // Keep activation behavior while preventing the system's resize
            // frame from being painted over the custom caption.
            message.LParam = (IntPtr)(-1);
            base.WndProc(ref message);
            return;
        }
        if (message.Msg == GetMinMaxInfo)
        {
            base.WndProc(ref message);
            var screen = Screen.FromHandle(message.HWnd);
            var area = screen.WorkingArea;
            var limits = (NativeLimits)Marshal.PtrToStructure(message.LParam, typeof(NativeLimits));
            limits.Position = new Point(area.Left - screen.Bounds.Left, area.Top - screen.Bounds.Top);
            limits.Size = new Point(area.Width, area.Height);
            Marshal.StructureToPtr(limits, message.LParam, false);
            return;
        }
        if (message.Msg == NcHitTest && !FullScreen && close != null)
        {
            long packed = message.LParam.ToInt64();
            Point client = PointToClient(new Point((short)(packed & 0xffff), (short)((packed >> 16) & 0xffff)));
            int edge = HitEdge(client);
            if (edge != 0)
            {
                message.Result = (IntPtr)edge;
                return;
            }
        }
        base.WndProc(ref message);
    }

    private int HitEdge(Point point)
    {
        if (WindowState != FormWindowState.Normal) return 0;
        bool left = point.X < Padding.Left, right = point.X >= ClientSize.Width - Padding.Right;
        if (point.Y < Padding.Top) return left ? 13 : right ? 14 : 12;
        if (point.Y >= ClientSize.Height - Padding.Bottom) return left ? 16 : right ? 17 : 15;
        return left ? 10 : right ? 11 : 0;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeLimits
    {
        public Point Reserved, Size, Position, MinTrackSize, MaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect { public int Left, Top, Right, Bottom; }

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out NativeRect rectangle);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref uint value, int size);
}

internal enum CaptionAction { Minimize, Maximize, Close }

internal sealed class CaptionButton : Button
{
    private readonly CaptionAction action;
    private bool restoring;

    internal CaptionButton(CaptionAction action)
    {
        this.action = action;
        FlatStyle = FlatStyle.Flat;
        FlatAppearance.BorderSize = 0;
        FlatAppearance.MouseOverBackColor = action == CaptionAction.Close
            ? Color.FromArgb(156, 54, 59) : Color.FromArgb(54, 56, 51);
        FlatAppearance.MouseDownBackColor = action == CaptionAction.Close
            ? Color.FromArgb(127, 41, 46) : Color.FromArgb(69, 71, 64);
        UseVisualStyleBackColor = false;
        BackColor = Color.FromArgb(21, 22, 21);
        TabStop = false;
        AccessibleName = action == CaptionAction.Close ? "Fechar" :
            action == CaptionAction.Minimize ? "Minimizar" : "Maximizar";
    }

    internal void ShowRestore(bool value)
    {
        if (restoring == value) return;
        restoring = value;
        AccessibleName = restoring ? "Restaurar" : "Maximizar";
        Invalidate();
    }

    protected override void OnPaint(PaintEventArgs args)
    {
        base.OnPaint(args);
        var g = args.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        float scale = Width / 36f;
        float x = Width / 2f, y = Height / 2f, r = 4.5f * scale;
        using (var pen = new Pen(Color.FromArgb(232, 231, 218), 1.2f * scale))
        {
            pen.StartCap = pen.EndCap = LineCap.Round;
            if (action == CaptionAction.Close)
            {
                g.DrawLine(pen, x - r, y - r, x + r, y + r);
                g.DrawLine(pen, x + r, y - r, x - r, y + r);
            }
            else if (action == CaptionAction.Minimize)
                g.DrawLine(pen, x - r, y + r * .5f, x + r, y + r * .5f);
            else if (restoring)
            {
                g.DrawLines(pen, new[] {
                    new PointF(x - r * .45f, y - r * .4f), new PointF(x - r * .45f, y - r * 1.22f),
                    new PointF(x + r * 1.22f, y - r * 1.22f), new PointF(x + r * 1.22f, y + r * .45f),
                    new PointF(x + r * .43f, y + r * .45f)
                });
                g.DrawRectangle(pen, x - r, y - r * .4f, r * 1.43f, r * 1.65f);
            }
            else
                g.DrawRectangle(pen, x - r, y - r, 2 * r, 2 * r);
        }
    }
}
