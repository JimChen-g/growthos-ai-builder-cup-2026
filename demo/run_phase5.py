"""Print or export the fixed Phase 5 three-minute demo rundown."""

import argparse

from backend.config import Settings
from backend.services.demo import load_demo_script


def format_time(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = Settings(_env_file=None)
    script = load_demo_script(settings.phase5_demo_fixture_path)
    if args.json:
        print(script.model_dump_json(indent=2))
        return

    print("GrowthOS Phase 5 · Fixed 3-Minute Demo")
    print(
        f"Case: {script.case.title} | Team: {script.case.team_size} people | "
        f"MRR: ${script.case.mrr:,.0f}"
    )
    print(f"Problems: {' · '.join(script.case.growth_problems)}")
    print()
    for stage in script.stages:
        time_range = f"{format_time(stage.start_seconds)}-{format_time(stage.end_seconds)}"
        print(f"{time_range}  {stage.label}: {stage.title}")
        print(f"           Cue: {stage.presenter_cue}")


if __name__ == "__main__":
    main()
