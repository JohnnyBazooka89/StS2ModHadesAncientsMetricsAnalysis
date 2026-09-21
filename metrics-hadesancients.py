#!/usr/bin/env python3
"""Import Slay the Spire 2 runs into PostgreSQL and export simple CSV reports."""
import argparse
import csv
import json
import re
import traceback
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values


def parse_bool(value):
    if isinstance(value, bool):
        return value
    value = value.lower()
    if value in ('true', 'yes', '1', 't'):
        return True
    if value in ('false', 'no', '0', 'f'):
        return False
    raise argparse.ArgumentTypeError('Expected true/false')


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--METRICS_PATH', required=True, type=Path)
    parser.add_argument('--DATABASE', required=True)
    parser.add_argument('--DATABASE_USER', required=True)
    parser.add_argument('--DATABASE_PASSWORD', required=True)
    parser.add_argument('--DATABASE_HOST', default='localhost')
    parser.add_argument('--DATABASE_PORT', default=5432, type=int)
    parser.add_argument('--REPORT_PATH', default='report-hadesancients', type=Path)
    parser.add_argument('--ANCIENT_MAP', default=Path(__file__).with_name('ancient-map.json'),
                        type=Path, help='JSON mapping ancient names to their relic IDs')
    parser.add_argument('--FIND_NEW_RUNS_TO_PROCESS', default=False, type=parse_bool)
    parser.add_argument('--PROCESS_RUNS', default=False, type=parse_bool)
    parser.add_argument('--MIN_ANCIENT_CHOICES', default=1, type=int,
                        help='Minimum number of times offered to include an ancient in reports')
    parser.add_argument('--CHARACTER', default=None, help='Optional filter for ancient reports')
    parser.add_argument('--ONLY_BASE_GAME_CHARACTERS', default=False, type=parse_bool,
                        help='Only include IRONCLAD, SILENT, REGENT, NECROBINDER, and DEFECT')
    parser.add_argument('--MIN_ASC_LEVEL', default=0, type=int,
                        help='Minimum ascension level to include (inclusive)')
    parser.add_argument('--MAX_ASC_LEVEL', default=10, type=int,
                        help='Maximum ascension level to include (inclusive)')
    parser.add_argument('--ASCENSION_MIN', default=None, type=int)
    parser.add_argument('--ASCENSION_MAX', default=None, type=int)
    parser.add_argument('--NUM_PLAYERS', default=None, type=int)
    return parser.parse_args()


RUN_FIELDS = (
    'hades_ancients_version', 'has_foreign_content', 'build_id', 'player_id',
    'character', 'win', 'num_players', 'build_type', 'ascension',
    'total_playtime', 'total_win_rate', 'num_reloads', 'run_playtime',
    'floor_reached', 'killed_by_encounter',
)
DATA_KEYS = {
    'build_id': 'buildId', 'player_id': 'playerId', 'num_players': 'numPlayers',
    'build_type': 'buildType', 'total_playtime': 'totalPlaytime',
    'total_win_rate': 'totalWinRate', 'num_reloads': 'numReloads',
    'run_playtime': 'runPlaytime', 'floor_reached': 'floorReached',
    'killed_by_encounter': 'killedByEncounter',
}


def extract_run(document):
    data = document['data']
    if not isinstance(data, dict) or not isinstance(data.get('win'), bool):
        raise ValueError('Missing data object or boolean data.win')
    result = {
        'hades_ancients_version': document.get('hades_ancients_version'),
        'has_foreign_content': document.get('has_foreign_content'),
    }
    for field in RUN_FIELDS[2:]:
        result[field] = data.get(DATA_KEYS.get(field, field))
    return result


def extract_ancients(document):
    choices = document['data'].get('ancientChoices', [])
    if not isinstance(choices, list):
        raise ValueError('data.ancientChoices must be an array')
    rows = []
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise ValueError(f'ancientChoices[{index}] must be an object')
        picked = choice.get('picked')
        skipped = choice.get('skipped', [])
        if not isinstance(skipped, list) or (picked is not None and not isinstance(picked, str)):
            raise ValueError(f'Invalid ancientChoices[{index}]')
        if picked is not None and not picked:
            raise ValueError(f'Empty picked ancient at index {index}')
        if any(not isinstance(item, str) or not item for item in skipped):
            raise ValueError(f'Invalid skipped ancient at index {index}')
        if picked is not None and picked in skipped:
            raise ValueError(f'Ancient appears as picked and skipped at index {index}')
        if len(set(skipped)) != len(skipped):
            raise ValueError(f'Duplicate skipped ancient at index {index}')
        if picked is not None:
            rows.append((index, picked, True))
        rows.extend((index, item, False) for item in skipped)
    return rows


def discover(conn, directory):
    count = 0
    with conn.cursor() as cur:
        for filepath in directory.rglob('*'):
            if not filepath.is_file():
                continue
            cur.execute('INSERT INTO run (file_path) VALUES (%s) ON CONFLICT DO NOTHING',
                        (str(filepath.resolve()),))
            count += cur.rowcount
    conn.commit()
    print(f'Discovered {count} new JSON files')


def process(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT file_path FROM run WHERE status = 'NEW' ORDER BY file_path")
        paths = [row[0] for row in cur.fetchall()]
    processed = errors = 0
    for filepath in paths:
        try:
            with open(filepath, encoding='utf-8') as handle:
                document = json.load(handle)
            run = extract_run(document)
            ancients = extract_ancients(document)
            with conn.cursor() as cur:
                assignments = ', '.join(f'{field} = %s' for field in RUN_FIELDS)
                cur.execute(f'''UPDATE run SET {assignments}, status = 'PROCESSED',
                               error_message = NULL WHERE file_path = %s''',
                            [run[field] for field in RUN_FIELDS] + [filepath])
                cur.execute('DELETE FROM ancient_choice WHERE run_file_path = %s', (filepath,))
                if ancients:
                    execute_values(cur, '''INSERT INTO ancient_choice
                        (run_file_path, choice_index, ancient_relic_id, picked) VALUES %s''',
                        [(filepath, index, ancient_relic_id, picked)
                         for index, ancient_relic_id, picked in ancients])
            conn.commit()
            processed += 1
        except Exception:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("UPDATE run SET status='ERROR', error_message=%s WHERE file_path=%s",
                            (traceback.format_exc(), filepath))
            conn.commit()
            errors += 1
            print(f'Failed to import: {filepath}')
    print(f'Processed {processed} runs; {errors} errors')


def write_csv(path, headers, rows):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f'Wrote {path}')


ANCIENT_REPORT_HEADERS = [
    'ancient_relic_id', 'offered_runs', 'picked_runs', 'skipped_runs', 'pick_pct',
    'picked_wins', 'picked_losses', 'picked_win_pct', 'skipped_wins',
    'skipped_losses', 'skipped_win_pct', 'win_pct_point_difference',
]

ANCIENT_SUMMARY_HEADERS = [
    'ancient', 'offered_runs', 'wins', 'losses', 'win_pct',
]



def load_ancient_map(path):
    """Load an ancient name -> relic IDs map; reject ambiguous assignments."""
    with path.open(encoding='utf-8') as handle:
        mapping = json.load(handle)
    if not isinstance(mapping, dict):
        raise ValueError('Ancient map must be a JSON object: name -> list of relic IDs')
    owner_by_relic = {}
    for ancient, relics in mapping.items():
        if not isinstance(ancient, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', ancient):
            raise ValueError(f'Invalid ancient name {ancient!r}; use letters, numbers, _ or -')
        if not isinstance(relics, list):
            raise ValueError(f'{ancient}: relic IDs must be an array')
        for relic in relics:
            if not isinstance(relic, str) or not relic:
                raise ValueError(f'{ancient}: invalid relic ID {relic!r}')
            if relic in owner_by_relic:
                raise ValueError(f'{relic!r} belongs to both {owner_by_relic[relic]!r} and {ancient!r} (or is repeated)')
            owner_by_relic[relic] = ancient
    return mapping


def report(conn, args):
    ancient_map = load_ancient_map(args.ANCIENT_MAP)
    args.REPORT_PATH.mkdir(parents=True, exist_ok=True)
    if args.MIN_ASC_LEVEL > args.MAX_ASC_LEVEL:
        raise ValueError('MIN_ASC_LEVEL must not exceed MAX_ASC_LEVEL')
    filters = ["r.status = 'PROCESSED'"]
    params = []
    if args.ONLY_BASE_GAME_CHARACTERS:
        filters.append("r.character IN ('IRONCLAD', 'SILENT', 'REGENT', 'NECROBINDER', 'DEFECT')")
    filters.append('r.ascension >= %s')
    params.append(args.MIN_ASC_LEVEL)
    filters.append('r.ascension <= %s')
    params.append(args.MAX_ASC_LEVEL)
    for column, value, operation in (
        ('character', args.CHARACTER, '='),
        ('ascension', args.ASCENSION_MIN, '>='),
        ('ascension', args.ASCENSION_MAX, '<='),
        ('num_players', args.NUM_PLAYERS, '='),
    ):
        if value is not None:
            filters.append(f'r.{column} {operation} %s')
            params.append(value)
    where = ' AND '.join(filters)
    with conn.cursor() as cur:
        cur.execute('SELECT status, COUNT(*) FROM run GROUP BY status ORDER BY status')
        write_csv(args.REPORT_PATH / '00_import_status.csv', ['status', 'runs'], cur.fetchall())

        for name, group_columns in (
            ('01_summary', []), ('02_by_character', ['character']),
            ('03_by_ascension', ['ascension']), ('04_by_player_count', ['num_players']),
            ('05_by_character_ascension_players', ['character', 'ascension', 'num_players']),
        ):
            group = ', '.join(f'r.{column}' for column in group_columns)
            select = (group + ', ') if group else ''
            group_by = f'GROUP BY {group}' if group else ''
            order_by = f'ORDER BY {group}' if group else ''
            cur.execute(f'''
                SELECT {select}COUNT(*) AS runs,
                       COUNT(*) FILTER (WHERE r.win) AS wins,
                       COUNT(*) FILTER (WHERE NOT r.win) AS losses,
                       ROUND(100.0 * COUNT(*) FILTER (WHERE r.win) / NULLIF(COUNT(*),0), 2) AS win_pct,
                       ROUND(AVG(r.run_playtime)::numeric, 1) AS avg_run_playtime,
                       ROUND(AVG(r.floor_reached)::numeric, 1) AS avg_floor_reached
                FROM run r WHERE {where} {group_by} {order_by}''', params)
            write_csv(args.REPORT_PATH / (name + '.csv'),
                      group_columns + ['runs', 'wins', 'losses', 'win_pct',
                                       'avg_run_playtime', 'avg_floor_reached'], cur.fetchall())

        # DISTINCT prevents one run appearing multiple times for the same ancient and outcome.
        # Offered-but-skipped win rate is conditional on this ancient actually being offered.
        cur.execute(f'''
            WITH offers AS (
                SELECT DISTINCT a.run_file_path, a.ancient_relic_id, a.picked, r.win
                FROM ancient_choice a JOIN run r ON r.file_path = a.run_file_path
                WHERE {where}
            ), counts AS (
                SELECT ancient_relic_id,
                    COUNT(*) FILTER (WHERE picked) AS picked_runs,
                    COUNT(*) FILTER (WHERE NOT picked) AS skipped_runs,
                    COUNT(*) FILTER (WHERE picked AND win) AS picked_wins,
                    COUNT(*) FILTER (WHERE NOT picked AND win) AS skipped_wins
                FROM offers GROUP BY ancient_relic_id
            )
            SELECT ancient_relic_id, picked_runs + skipped_runs AS offered_runs,
                picked_runs, skipped_runs,
                ROUND(100.0 * picked_runs / NULLIF(picked_runs + skipped_runs, 0), 2) AS pick_pct,
                picked_wins, picked_runs - picked_wins AS picked_losses,
                ROUND(100.0 * picked_wins / NULLIF(picked_runs, 0), 2) AS picked_win_pct,
                skipped_wins, skipped_runs - skipped_wins AS skipped_losses,
                ROUND(100.0 * skipped_wins / NULLIF(skipped_runs, 0), 2) AS skipped_win_pct,
                ROUND(100.0 * picked_wins / NULLIF(picked_runs, 0) -
                      100.0 * skipped_wins / NULLIF(skipped_runs, 0), 2) AS win_pct_point_difference
            FROM counts WHERE picked_runs + skipped_runs >= %s
            ORDER BY offered_runs DESC, ancient_relic_id
        ''', params + [args.MIN_ANCIENT_CHOICES])
        ancient_rows = cur.fetchall()
        write_csv(args.REPORT_PATH / '06_ancient_choices.csv',
                  ANCIENT_REPORT_HEADERS, ancient_rows)

        # The map is applied at report time, so changing it never requires reimporting runs.
        rows_by_id = {row[0]: row for row in ancient_rows}
        for ancient, relic_ids in ancient_map.items():
            # Preserve map ordering. Missing relics are omitted (no offers in filtered runs).
            rows = [rows_by_id[relic_id] for relic_id in relic_ids if relic_id in rows_by_id]
            write_csv(args.REPORT_PATH / f'06_ancient_choices_{ancient}.csv',
                      ANCIENT_REPORT_HEADERS, rows)

        # Count each run once per Ancient, even if it offered multiple mapped relics.
        # Keep the overall summary and additionally split it by ascension, character,
        # and their combination. Each breakdown applies the minimum to each group.
        ancient_order = {name: index for index, name in enumerate(ancient_map)}
        for filename, group_columns in (
            ('07_ancient_summary.csv', []),
            ('07_ancient_summary_by_ascension.csv', ['ascension']),
            ('07_ancient_summary_by_character.csv', ['character']),
            ('07_ancient_summary_by_ascension_character.csv', ['ascension', 'character']),
        ):
            dimensions = ', '.join(group_columns)
            run_dimensions = ''.join(f', r.{column}' for column in group_columns)
            grouped_dimensions = f', {dimensions}' if dimensions else ''
            cur.execute(f'''
                WITH relic_map AS (
                    SELECT entry.key AS ancient,
                           jsonb_array_elements_text(entry.value) AS ancient_relic_id
                    FROM jsonb_each(%s::jsonb) AS entry
                ), run_ancients AS (
                    SELECT DISTINCT m.ancient, a.run_file_path, r.win{run_dimensions}
                    FROM relic_map m
                    JOIN ancient_choice a ON a.ancient_relic_id = m.ancient_relic_id
                    JOIN run r ON r.file_path = a.run_file_path
                    WHERE {where}
                ), counts AS (
                    SELECT ancient{grouped_dimensions}, COUNT(*) AS offered_runs,
                           COUNT(*) FILTER (WHERE win) AS wins
                    FROM run_ancients GROUP BY ancient{grouped_dimensions}
                )
                SELECT ancient{grouped_dimensions}, offered_runs, wins,
                       offered_runs - wins AS losses,
                       ROUND(100.0 * wins / NULLIF(offered_runs, 0), 2) AS win_pct
                FROM counts WHERE offered_runs >= %s
            ''', [json.dumps(ancient_map)] + params + [args.MIN_ANCIENT_CHOICES])
            # Retain the ancient-map.json ordering, then sort each breakdown.
            summary_rows = sorted(
                cur.fetchall(),
                key=lambda row: (ancient_order[row[0]],
                                 *((value is None, value) for value in row[1:1 + len(group_columns)])),
            )
            write_csv(args.REPORT_PATH / filename,
                      ['ancient'] + group_columns + ANCIENT_SUMMARY_HEADERS[1:],
                      summary_rows)


def main():
    args = parse_args()
    with psycopg2.connect(dbname=args.DATABASE, user=args.DATABASE_USER,
                          password=args.DATABASE_PASSWORD, host=args.DATABASE_HOST,
                          port=args.DATABASE_PORT) as conn:
        if args.FIND_NEW_RUNS_TO_PROCESS:
            discover(conn, args.METRICS_PATH)
        if args.PROCESS_RUNS:
            process(conn)
        report(conn, args)


if __name__ == '__main__':
    main()
