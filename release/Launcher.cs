// MIT; small Windows launcher. Uses only paths relative to this executable.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
[assembly: AssemblyTitle("Stimulus Harvester")]
[assembly: AssemblyDescription("Research stimulus discovery & adjudication")]
[assembly: AssemblyCompany("Stimulus Harvester contributors")]
[assembly: AssemblyProduct("Stimulus Harvester")]
[assembly: AssemblyVersion("0.2.0.4")]
[assembly: AssemblyFileVersion("0.2.0.4")]
[assembly: AssemblyInformationalVersion("0.2.0rc4")]
class Launcher {
    // Windows CommandLineToArgvW quoting, including quotes and trailing backslashes.
    static string Quote(string value) {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"'); return result.ToString();
    }
    [STAThread] static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
#if CLI
        const bool gui = false;
        string runtime = "python.exe", module = "stimulus_harvester.cli";
#else
        const bool gui = true;
        string runtime = "pythonw.exe", module = "stimulus_harvester.gui.app";
#endif
        var command = new StringBuilder("-I -B -m " + module);
        foreach (string arg in args) command.Append(" " + Quote(arg));
        try {
            var start = new ProcessStartInfo(Path.Combine(root, "runtime", runtime), command.ToString());
            start.UseShellExecute = false; start.CreateNoWindow = gui;
            start.RedirectStandardError = gui;
            using (var process = Process.Start(start)) {
                string errors = gui ? process.StandardError.ReadToEnd() : "";
                process.WaitForExit();
                if (gui && process.ExitCode != 0) {
                    string logs = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StimulusHarvester", "logs");
                    Directory.CreateDirectory(logs);
                    string path = Path.Combine(logs, "last-startup-error.txt");
                    File.WriteAllText(path, errors);
                    MessageBox.Show("Stimulus Harvester could not finish. Diagnostic details: " + path,
                        "Stimulus Harvester", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return process.ExitCode;
            }
        } catch (Exception error) {
            if (gui) MessageBox.Show(error.Message, "Stimulus Harvester launch error");
            else Console.Error.WriteLine(error.Message);
            return 1;
        }
    }
}
