import csv
import html
import io
import json


def export_job(job, format):
    marker = 'SIMULATED' if job['mode'] == 'demo' else 'LIVE'
    if format == 'json':
        return json.dumps(dict(job, report_marker=marker), ensure_ascii=False, indent=2).encode(), 'application/json; charset=utf-8'
    if format == 'html':
        esc = lambda value: html.escape(str(value), quote=True)
        # Complete evidence is retained, including the original configuration snapshot.
        content = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Acceptance report</title><body><h1>' + marker + ' Acceptance report</h1><p>Scope: communication path / PCI passive state. Business and electrical function require separate verification.</p><pre>' + esc(json.dumps(job, ensure_ascii=False, indent=2)) + '</pre></body></html>'
        return content.encode(), 'text/html; charset=utf-8'
    if format == 'csv':
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        columns = ['report_marker', 'job_id', 'status', 'mode', 'scenario', 'station_id', 'batch', 'operator', 'notes', 'config_hash', 'config_snapshot', 'created_at', 'finished_at', 'device_id', 'name', 'connection_type', 'interface', 'protocol', 'scope', 'verdict', 'fault_code', 'summary', 'suggestion', 'request', 'response', 'duration_ms', 'simulated']
        writer.writerow(columns)
        def safe(value):
            text = str(value if value is not None else '')
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text
        for result in job['results']:
            row = dict(job, **job['metadata'], **result, report_marker=marker, job_id=job['id'], config_snapshot=json.dumps(job['config_snapshot'], ensure_ascii=False))
            writer.writerow([safe(row.get(key, '')) for key in columns])
        return stream.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8'
    raise ValueError('Unsupported export format')
