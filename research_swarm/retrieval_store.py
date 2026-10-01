"""Atomic ingestion into an owned task, with immutable source/discovery history."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

from .retrieval import doi, identifiers, merge_paper, same_paper


_DDL = '''
CREATE TABLE IF NOT EXISTS LiteratureSearchRun (
  RunID INTEGER PRIMARY KEY, CreatedAt TEXT NOT NULL, SummaryJson TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS LiteratureDiscovery (
  RunID INTEGER NOT NULL, PaperID INTEGER NOT NULL, DiscoveryJson TEXT NOT NULL,
  UNIQUE(RunID, PaperID, DiscoveryJson));
CREATE TABLE IF NOT EXISTS LiteratureCitation (
  FromPaperID INTEGER NOT NULL, ToPaperID INTEGER NOT NULL, SourceType TEXT NOT NULL,
  RunID INTEGER NOT NULL, CreatedAt TEXT NOT NULL,
  UNIQUE(FromPaperID, ToPaperID, SourceType));
CREATE TABLE IF NOT EXISTS LiteratureIdentifier (
  Identifier TEXT PRIMARY KEY, PaperID INTEGER NOT NULL);
'''
_FIELDS = ('Title', 'Abstract', 'Year', 'Venue', 'DOI', 'ArxivId', 'S2PaperId',
           'OpenAlexId', 'Authors', 'CitationCount', 'Publisher')


def ingest(db, result, query, top_k, node_id=None):
    """Keep all bounded candidates; top_k only limits the model's reading list."""
    now = datetime.now(timezone.utc).isoformat()
    summary = dict(result['summary'])
    with closing(sqlite3.connect(db, timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
        with conn:
            existing = [dict(r) for r in conn.execute('SELECT * FROM Paper')]
            by_id = {r['PaperID']: r for r in existing}
            aliases = {r['Identifier']: r['PaperID'] for r in conn.execute('SELECT * FROM LiteratureIdentifier') if r['PaperID'] in by_id}
            for row in existing:
                for key in identifiers(row):
                    aliases.setdefault(key, row['PaperID'])
            if node_id is None:
                root = conn.execute('SELECT NodeID FROM FacetNode WHERE IsSearchRoot=1 ORDER BY Priority,NodeID LIMIT 1').fetchone()
                node_id = root[0] if root else None
            elif not conn.execute('SELECT 1 FROM FacetNode WHERE NodeID=?', (node_id,)).fetchone():
                raise ValueError('检索节点不属于当前论文库')
            run_id = conn.execute('INSERT INTO LiteratureSearchRun(CreatedAt,SummaryJson) VALUES (?,?)',
                                  (now, '{}')).lastrowid
            ids, new_ids, identity_conflicts = [], [], 0
            for paper in result['papers']:
                matches = sorted({aliases[k] for k in identifiers(paper) if k in aliases})
                if not matches:
                    matches = [r['PaperID'] for r in existing if same_paper(r, paper)]
                if matches:
                    pid = matches[0]
                    # Never delete historical IDs or rewrite their evidence anchors.
                    # A new bridge can reveal old duplicates; report the ambiguity.
                    identity_conflicts += int(len(matches) > 1)
                    row = by_id[pid]
                    merged = merge_paper(dict(row), paper)
                    for field in ('DOI', 'OpenAlexId'):
                        if merged.get(field) and merged.get(field) != row.get(field):
                            owner = conn.execute(f'SELECT PaperID FROM Paper WHERE {field}=?', (merged[field],)).fetchone()
                            if owner and owner[0] != pid:
                                merged[field] = row.get(field)
                    conn.execute('UPDATE Paper SET ' + ','.join(f'{f}=?' for f in _FIELDS) + ' WHERE PaperID=?',
                                 tuple(merged.get(f) for f in _FIELDS) + (pid,))
                    row.update({f: merged.get(f) for f in _FIELDS})
                else:
                    values = dict(paper, DOI=doi(paper.get('DOI')))
                    pid = conn.execute('INSERT INTO Paper (' + ','.join(_FIELDS) + ',ImportedAt) VALUES (' +
                        ','.join('?' for _ in range(len(_FIELDS) + 1)) + ')',
                        tuple(values.get(f) for f in _FIELDS) + (now,)).lastrowid
                    row = dict(values, PaperID=pid)
                    existing.append(row)
                    by_id[pid] = row
                    new_ids.append(str(pid))
                if pid not in ids:
                    ids.append(pid)
                for key in identifiers(paper):
                    aliases.setdefault(key, pid)
                    conn.execute('INSERT OR IGNORE INTO LiteratureIdentifier VALUES (?,?)', (key, aliases[key]))
                for record in paper.get('records', []):
                    # Preserve each source's actual abstract and identifiers rather
                    # than attributing the merged record to an arbitrary provider.
                    raw = json.dumps(record['metadata'], ensure_ascii=False, sort_keys=True)
                    if not conn.execute('SELECT 1 FROM SrcRecord WHERE PaperID=? AND SourceType=? AND ExternalId=? AND RawJson=?',
                                        (pid, record['source'], record['externalId'], raw)).fetchone():
                        conn.execute('INSERT INTO SrcRecord(PaperID,SourceType,ExternalId,URL,RawJson,FetchedAt) VALUES (?,?,?,?,?,?)',
                                     (pid, record['source'], record['externalId'], record['url'], raw, now))
                    abstract = record['metadata'].get('Abstract') or ''
                    extractor = 'metadata:' + record['source']
                    if abstract and not conn.execute('SELECT 1 FROM Evidence WHERE PaperID=? AND EvType=? AND QuoteText=? AND Extractor=?',
                                                     (pid, 'abstract', abstract, extractor)).fetchone():
                        conn.execute('INSERT INTO Evidence(PaperID,EvType,QuoteText,Locator,Extractor,Confidence,CreatedAt) VALUES (?,?,?,?,?,?,?)',
                                     (pid, 'abstract', abstract, 'Abstract', extractor, .5, now))
                for discovery in paper.get('discoveries', []):
                    conn.execute('INSERT OR IGNORE INTO LiteratureDiscovery VALUES (?,?,?)',
                                 (run_id, pid, json.dumps(discovery, ensure_ascii=False, sort_keys=True)))
                if node_id is not None:
                    conn.execute('INSERT OR IGNORE INTO PaperFacet(PaperID,NodeID,Confidence,IsHumanConfirmed,CreatedAt) VALUES (?,?,?,0,?)',
                                 (pid, node_id, .5, now))
            relation = conn.execute("SELECT RelTypeID FROM RelationType WHERE TypeName='cites' AND IsDirected=1").fetchone()
            relation_id = relation[0] if relation else conn.execute(
                "INSERT INTO RelationType(TypeName,IsDirected,Description) VALUES ('cites',1,'公开论文 API 返回的引用关系；不代表支持或反对')").lastrowid
            retained_edges = set()
            for edge in result['edges']:
                start = next((aliases[k] for k in edge['from'] if k in aliases), None)
                end = next((aliases[k] for k in edge['to'] if k in aliases), None)
                if start is None or end is None or start == end:
                    continue
                retained_edges.add((start, end))
                conn.execute('INSERT OR IGNORE INTO LiteratureCitation VALUES (?,?,?,?,?)', (start, end, edge['source'], run_id, now))
                if not conn.execute('SELECT 1 FROM RelEdge WHERE RelTypeID=? AND FromPaperID=? AND ToPaperID=?',
                                    (relation_id, start, end)).fetchone():
                    conn.execute('INSERT INTO RelEdge(RelTypeID,FromPaperID,ToPaperID,Weight,Confidence,CreatedAt) VALUES (?,?,?,?,?,?)',
                                 (relation_id, start, end, 1, 1, now))
            summary.update(runId=str(run_id), newPaperIds=new_ids, candidatePaperIds=[str(i) for i in ids],
                           resultPaperIds=[str(i) for i in ids[:top_k]], topK=top_k, nodeId=str(node_id) if node_id else None,
                           resultLookupComplete=True, retainedCitationEdges=len(retained_edges),
                           historicalIdentityConflicts=identity_conflicts)
            conn.execute('UPDATE LiteratureSearchRun SET SummaryJson=? WHERE RunID=?',
                         (json.dumps(summary, ensure_ascii=False), run_id))
    return summary
