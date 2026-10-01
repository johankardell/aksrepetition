import pathlib
import re
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDES = [ROOT / "README.md", ROOT / ".github/copilot-instructions.md", *sorted((ROOT / "labs").glob("*.md"))]


class LabCommandsTests(unittest.TestCase):
    def test_bash_examples_parse_without_execution(self):
        for guide in GUIDES:
            for block in re.finditer(r"^```bash\n(.*?)^```", guide.read_text(), re.M | re.S):
                line = guide.read_text()[:block.start()].count("\n") + 1
                with self.subTest(guide=guide.relative_to(ROOT), line=line):
                    result = subprocess.run(["bash", "-n"], input=block[1],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_references_exist_and_are_lowercase(self):
        for guide in GUIDES:
            for path in re.findall(r"(?:scripts|ops|advanced)/[A-Za-z0-9_-]+\.sh\b", guide.read_text()):
                with self.subTest(guide=guide.relative_to(ROOT), script=path):
                    self.assertEqual(path, path.lower())
                    self.assertTrue((ROOT / path).is_file(), f"Missing helper: {path}")
        for directory in ("scripts", "ops", "advanced"):
            for path in (ROOT / directory).glob("*.sh"):
                self.assertEqual(path.name, path.name.lower())
            self.assertEqual(list((ROOT / directory).glob("*.ps1")), [])

    def test_guides_and_workflow_have_no_powershell_commands(self):
        for path in [*GUIDES, ROOT / ".github/workflows/publish-image.yml"]:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(path.read_text(), r"(?i)```powershell|\.ps1\b|\bpwsh\b|\$env:")

    def test_scripts_and_embedded_python_parse_without_execution(self):
        for directory in ("scripts", "ops", "advanced"):
            for path in (ROOT / directory).glob("*.sh"):
                with self.subTest(script=path.relative_to(ROOT)):
                    for shell in ("bash", "zsh"):
                        if shutil.which(shell):
                            result = subprocess.run([shell, "-n", str(path)], text=True, capture_output=True)
                            self.assertEqual(result.returncode, 0, result.stderr)
                    for block in re.findall(r"<<'PY'\n(.*?)\nPY", path.read_text(), re.S):
                        compile(block, str(path), "exec")

    def test_workflow_bash_steps_parse_without_execution(self):
        workflow = (ROOT / ".github/workflows/publish-image.yml").read_text()
        blocks = re.findall(r"^        run: \|\n((?:^          .*\n|^\n)*)", workflow, re.M)
        self.assertGreaterEqual(len(blocks), 2)
        for block in blocks:
            script = "\n".join(line[10:] for line in block.splitlines())
            result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
