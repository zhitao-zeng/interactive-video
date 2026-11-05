"""Normal prepare/run/status entrypoint for isolated episode production."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from novel_manga.application.production.common import parse_chapters
from novel_manga.application.agents.sandbox import load_config

from .context import create_context
from .flow import run_episode
from .lifecycle import read_status
from .runtime import process_environment
from .inputs import prepare_inputs, refresh_character_inputs


def parser():
    result = argparse.ArgumentParser(description='Prepare, run or inspect one sandbox agent per episode.')
    commands = result.add_subparsers(dest='command', required=True)
    for name, help_text in (('prepare', 'Copy the episode inputs only; no models or rendering.'),
                            ('run', 'Run the episode agent and its existing host media tools.'),
                            ('status', 'Read the existing episode run state.')):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--novel-dir', type=Path, required=True)
        selection = command.add_mutually_exclusive_group(required=True)
        selection.add_argument('--episode', help='Episode directory name or chapter number.')
        selection.add_argument('--chapters', help='Existing chapter range syntax, for example 1-3,7.')
        command.add_argument('--input-mode', choices=('auto', 'source', 'script'), default='auto')
        command.add_argument('--existing-production', action='store_true',
                             help='Take over a prepared production in place, retaining tasks, takes and budgets.')
        command.add_argument('--output-root', type=Path)
        command.add_argument('--project-root', type=Path,
                             help='Runtime project containing .env, configuration and media helpers.')
        command.add_argument('--target-seconds', type=float, nargs='+', metavar='SECONDS',
                             help='Source episode duration or minimum and maximum duration.')
        if name == 'prepare':
            command.add_argument('--refresh-character', action='append', metavar='ASSET_ID',
                                 help='Refresh only this character in an existing input snapshot; repeat to select more.')
        if name == 'run':
            command.add_argument('--config', type=Path, help='Task-specific sandbox configuration.')
            command.add_argument('--retry-issues', action='store_true',
                                 help='Retry a stopped episode, retaining media and video budgets; session policy follows config.')
            command.add_argument('--timeout', type=int,
                                 help='Total operation continuation time; defaults to sandbox configuration.')
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    results = []
    try:
        episodes = parse_chapters(arguments.chapters) if arguments.chapters is not None else [arguments.episode]
        if not episodes:
            raise ValueError('choose at least one episode')
        target = arguments.target_seconds
        if target is not None:
            if len(target) not in (1, 2):
                raise ValueError('--target-seconds needs one duration or a minimum/maximum pair')
            target = target[0] if len(target) == 1 else tuple(target)
        overrides = ({'NOVEL_PROJECT_ROOT': str(arguments.project_root.expanduser().resolve())}
                     if arguments.project_root else {})
        with process_environment(overrides):
            for episode in episodes:
                try:
                    options = {'existing_production': True} if arguments.existing_production else {}
                    context = create_context(arguments.novel_dir, episode,
                        output_root=arguments.output_root, input_mode=arguments.input_mode,
                        target_seconds=target, **options)
                    if arguments.command == 'prepare':
                        refreshed = (refresh_character_inputs(context, arguments.refresh_character)
                                     if arguments.refresh_character else None)
                        if refreshed is None:
                            prepare_inputs(context)
                        result = {'episode': context.source_episode_dir.name,
                                  'status': 'refreshed' if refreshed else 'prepared',
                                  'input_mode': context.input_mode, 'run_dir': str(context.run_dir), 'exit_code': 0}
                        if refreshed:
                            result.update(refreshed)
                    elif arguments.command == 'status':
                        result = read_status(context)
                    else:
                        options = {'retry_issues': True} if arguments.retry_issues else {}
                        if arguments.config:
                            options['config'] = load_config(arguments.config)
                        result = run_episode(context, timeout=arguments.timeout, **options)
                        while arguments.existing_production:
                            from .recovery import idle_fallback_available
                            sliced = (result.get('status') == 'deferred'
                                and result.get('reason') == 'Agent reached this slice time limit; continue the saved session'
                                and float(result.get('remaining_seconds') or 0) > 0)
                            configured = options.get('config')
                            if configured is None and result.get('status') == 'blocked' and len(result.get('attempts') or []) >= 2:
                                configured = load_config(context.project_root/'configs/agent_sandbox.json')
                            fallback = idle_fallback_available(result, configured or {})
                            if not sliced and not fallback:
                                break
                            # A standalone takeover has no batch scheduler to
                            # resume it. Reuse the saved UUID and remaining time;
                            # retry_issues would open another operation window.
                            print(json.dumps({'event': 'use_configured_fallback' if fallback else 'resume_saved_episode',
                                'episode': context.source_episode_dir.name,
                                'remaining_seconds': result['remaining_seconds']},
                                ensure_ascii=False), flush=True)
                            continuation = {k: v for k, v in options.items() if k != 'retry_issues'}
                            result = run_episode(context, **continuation)
                    results.append(result)
                except (OSError, ValueError, RuntimeError) as error:
                    results.append({'episode': str(episode), 'status': 'blocked', 'passed': False,
                                    'exit_code': 2, 'reason': f'{type(error).__name__}: {error}'})
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({'command': arguments.command, 'episodes': results}, ensure_ascii=False, indent=2))
    return max((row.get('exit_code', 0) for row in results), default=0)


if __name__ == '__main__':
    raise SystemExit(main())
