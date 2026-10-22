"""Entry point: blender --background --factory-startup --python tests/run_all.py [-- filter]"""

import glob
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402

flt = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else ""
tests = []
for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
    spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in dir(mod):
        if attr.startswith("test_") and flt in attr:
            tests.append((f"{mod.__name__}.{attr}", getattr(mod, attr)))

failed = harness.run(tests)
print(f"\n{len(tests) - failed}/{len(tests)} passed")
sys.stdout.flush()
os._exit(1 if failed else 0)
