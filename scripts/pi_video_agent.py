"""Default fully-agent production entry for new editions."""
from pathlib import Path
import importlib.util


def main():
    path = Path(__file__).resolve().parents[1] / 'examples/pi-video-agent/starter.py'
    spec = importlib.util.spec_from_file_location('pi_agent_starter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


if __name__ == '__main__':
    main()
