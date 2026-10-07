"""Validate structured synthetic references and build an insert-only migration."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy' / 'template_expansion.json'
TABLE = 'AGENTSHIELD_DB.CORE.ATTACK_TEMPLATES'


def load_templates(path=SOURCE):
    source = json.loads(path.read_text())
    expected_additions = {'prompt_injection': 4, 'scope_violation': 4, 'pii_extraction': 5,
                          'social_engineering': 9, 'multi_turn': 9, 'data_exfiltration': 10,
                          'role_escalation': 10, 'indirect_injection': 14}
    if source['version'] != 'v2_library_1' or set(source['categories']) != set(expected_additions):
        raise ValueError('LIBRARY_VERSION_OR_CATEGORIES')
    templates, seen = [], set()
    for category, group in source['categories'].items():
        if len(group['cases']) != expected_additions[category] or group['existing_count'] + len(group['cases']) != 15:
            raise ValueError('CATEGORY_TOTAL')
        for index, case in enumerate(group['cases'], 1):
            turns = case['turns']
            if not 1 <= len(turns) <= 3 or any(not isinstance(turn, str) or not turn.strip() or len(turn) > 4000 for turn in turns):
                raise ValueError('INVALID_TURNS')
            if category == 'multi_turn' and len(turns) < 2:
                raise ValueError('MULTITURN_REQUIRED')
            fingerprint = tuple(' '.join(turn.split()).casefold() for turn in turns)
            if fingerprint in seen:
                raise ValueError('DUPLICATE_PROMPT')
            seen.add(fingerprint)
            if not case['name'].strip() or not group['expected_behavior'].strip():
                raise ValueError('MISSING_POLICY_OR_NAME')
            if case['surface'] not in ('user_request', 'conversation', 'quoted_document', 'existing_poisoned_playbook'):
                raise ValueError('UNKNOWN_SURFACE')
            templates.append({'ID': f'v2lib1_{category}_{index:02d}', 'CATEGORY': category,
                              'NAME': case['name'], 'PROMPT': turns[0], 'SEVERITY': 'high',
                              'EXPECTED_BEHAVIOR': group['expected_behavior'],
                              'TURNS': turns if len(turns) > 1 else None, 'DEMO': False})
    return templates


def migration(templates):
    payload = json.dumps(templates, ensure_ascii=True).replace('\\', '\\\\').replace("'", "''")
    # Staging is session-local. The existing campaign mutex serializes this update
    # with campaign admission; primary keys on standard tables are not enforced.
    comparisons = ' OR '.join('NOT EQUAL_NULL(t.' + column + ', s.' + column + ')'
                              for column in ('CATEGORY', 'NAME', 'PROMPT', 'SEVERITY', 'EXPECTED_BEHAVIOR', 'DEMO'))
    comparisons += ' OR NOT EQUAL_NULL(TO_JSON(t.TURNS), TO_JSON(s.TURNS))'
    return """USE ROLE ACCOUNTADMIN;
USE WAREHOUSE AGENTSHIELD_WH;
CREATE OR REPLACE TEMPORARY TABLE AGENTSHIELD_DB.ORCH.TEMPLATE_EXPANSION_STAGE AS
SELECT value:ID::VARCHAR ID, value:CATEGORY::VARCHAR CATEGORY, value:NAME::VARCHAR NAME,
       value:PROMPT::VARCHAR PROMPT, value:SEVERITY::VARCHAR SEVERITY,
       value:EXPECTED_BEHAVIOR::VARCHAR EXPECTED_BEHAVIOR,
       IFF(IS_NULL_VALUE(value:TURNS), NULL, value:TURNS)::ARRAY TURNS, value:DEMO::BOOLEAN DEMO
FROM TABLE(FLATTEN(INPUT => PARSE_JSON('""" + payload + """')));
EXECUTE IMMEDIATE $$
DECLARE
  conflicts INTEGER;
  invalid_state EXCEPTION (-20001, 'Template migration blocked: active campaign, duplicate IDs, mutex or conflicting content');
BEGIN
  BEGIN TRANSACTION;
  UPDATE AGENTSHIELD_DB.CORE.CAMPAIGN_MUTEX SET VERSION = VERSION + 1 WHERE ID = 1;
  IF (SQLROWCOUNT <> 1) THEN RAISE invalid_state; END IF;
  SELECT COUNT(*) INTO :conflicts FROM AGENTSHIELD_DB.CORE.CAMPAIGNS
    WHERE STATUS NOT IN ('COMPLETE','PARTIAL','FAILED','CANCELLED');
  IF (conflicts <> 0) THEN RAISE invalid_state; END IF;
  SELECT COUNT(*) INTO :conflicts FROM (SELECT ID FROM """ + TABLE + """ GROUP BY ID HAVING COUNT(*) > 1);
  IF (conflicts <> 0) THEN RAISE invalid_state; END IF;
  SELECT COUNT(*) INTO :conflicts FROM """ + TABLE + """ t
    JOIN AGENTSHIELD_DB.ORCH.TEMPLATE_EXPANSION_STAGE s ON t.ID = s.ID
    WHERE """ + comparisons + """;
  IF (conflicts <> 0) THEN RAISE invalid_state; END IF;
  MERGE INTO """ + TABLE + """ t USING AGENTSHIELD_DB.ORCH.TEMPLATE_EXPANSION_STAGE s ON t.ID = s.ID
    WHEN NOT MATCHED THEN INSERT (ID, CATEGORY, NAME, PROMPT, SEVERITY, EXPECTED_BEHAVIOR, TURNS, DEMO)
    VALUES (s.ID, s.CATEGORY, s.NAME, s.PROMPT, s.SEVERITY, s.EXPECTED_BEHAVIOR, s.TURNS, s.DEMO);
  COMMIT;
  RETURN 'Template expansion applied; existing rows unchanged';
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;
"""


def build(destination):
    templates = load_templates()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(migration(templates))
    print(json.dumps({'new_references': len(templates), 'categories': 8, 'output': str(destination)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build' / 'campaigns' / 'expand_templates.sql')
    build(parser.parse_args().output)