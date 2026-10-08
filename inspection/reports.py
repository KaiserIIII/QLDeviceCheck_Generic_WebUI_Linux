import csv
import html
import io
import json

from .domain import catalog_devices


REPORT_EN = {
    '仅选定设备': 'Selected devices only', '完整配置设备': 'All configured devices',
    'SIMULATED · 合成演示，不能作为真实设备验收凭据。': 'SIMULATED · Synthetic demonstration; cannot be used as live acceptance evidence.',
    'LIVE · 结论仅覆盖本次通信路径及 PCI 被动状态；电气性能、业务功能需另行验证。': 'LIVE · Findings cover the selected communication paths and passive PCI state. Electrical performance and business functionality require separate validation.',
    '通过': 'Passed', '未通过': 'Failed', '需复核': 'Review required', '未执行': 'Not run',
    '工位 / 设备编号': 'Station / unit ID', '批次': 'Batch', '操作者': 'Operator', '备注': 'Notes',
    '任务 ID': 'Task ID', '复测源任务': 'Parent task', '任务状态': 'Execution status',
    '创建时间 UTC': 'Created (UTC)', '结束时间 UTC': 'Finished (UTC)', '耗时 ms': 'Duration (ms)',
    '配置 SHA-256': 'Configuration SHA-256', 'PCI 被动状态': 'Passive PCI state', '通信路径': 'Communication path',
    '排查建议：': 'Suggested next step: ', '请求 / Request': 'Request', '响应 / Response': 'Response',
    '逐项探测 / Attempts': 'Probe attempts', '载体支持证据 / Supporting checks': 'Supporting carrier checks',
    '通过 {passed} · 未通过 {failed} · 需复核 {review} · 未执行 {not_run}': 'Passed {passed} · Failed {failed} · Review {review} · Not run {not_run}',
    '工控设备验收报告': 'Industrial device acceptance report', '台': 'devices',
    '部分设备复测通过不表示整机恢复。任务中止、取消或失败时，已收集的证据仍保留，结论需复核。': 'A partial retest does not establish whole-unit recovery. Evidence remains available after interruption, cancellation or failure; review the conclusion.',
    '验收记录': 'Acceptance record', '设备结论': 'Device results', '设备 ID': 'Device ID',
    '设备名称': 'Device name', '接口': 'Interface', '验证范围': 'Inspection scope', '结论': 'Verdict',
    '故障类别': 'Fault category', '测试证据': 'Original test evidence',
    '配置快照与任务事件': 'Configuration snapshot and task events',
    'SHA-256 用于识别配置版本；本报告未使用数字签名，不提供防篡改证明。': 'SHA-256 identifies the configuration version. This report is not digitally signed and does not prove tamper resistance.',
}


def html_report(job, marker, lang='zh'):
    """A portable, printable report; evidence and snapshot travel with the file."""
    def esc(value):
        return html.escape(str(value if value is not None else '—'), quote=True)
    def code(value):
        return '<pre>' + esc(value) + '</pre>'
    if lang not in ('zh', 'en'):
        raise ValueError('Unsupported report language')
    def tr(text):
        return REPORT_EN.get(text, text) if lang == 'en' else text
    expected = len(catalog_devices(job['config_snapshot']))
    selected = len(job['device_ids'])
    scope = tr('仅选定设备' if selected != expected else '完整配置设备')
    notice = ('SIMULATED · 合成演示，不能作为真实设备验收凭据。' if job['mode'] == 'demo'
              else 'LIVE · 结论仅覆盖本次通信路径及 PCI 被动状态；电气性能、业务功能需另行验证。')
    labels = {'PASS': '通过', 'FAIL': '未通过', 'REVIEW': '需复核', 'NOT_RUN': '未执行'}
    metadata = [(label, job['metadata'].get(key, '')) for key, label in
                [('station_id', '工位 / 设备编号'), ('batch', '批次'), ('operator', '操作者'), ('notes', '备注')]]
    metadata += [('任务 ID', job['id']), ('复测源任务', job.get('parent_job_id')), ('任务状态', job['status']),
                 ('创建时间 UTC', job['created_at']), ('结束时间 UTC', job['finished_at']),
                 ('耗时 ms', job['duration_ms']), ('配置 SHA-256', job['config_hash'])]
    notice = tr(notice)
    labels = {key: tr(value) for key, value in labels.items()}
    info = ''.join('<div><dt>' + esc(tr(k)) + '</dt><dd>' + esc(v) + '</dd></div>' for k, v in metadata)
    rows, evidence = [], []
    for result in job['results']:
        verdict = result['verdict']
        scope_name = tr('PCI 被动状态' if result['scope'] == 'pci_passive' else '通信路径')
        rows.append('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in [result['device_id'], result['name'],
                    result['interface'], scope_name, labels[verdict] + ' / ' + verdict, result['fault_code'], result['duration_ms']]) + '</tr>')
        evidence.append('<section class="evidence"><h3>' + esc(result['name']) + ' · ' + esc(result['device_id']) + '</h3><p>' +
                        esc(result['summary']) + '</p><p>' + esc(tr('排查建议：')) + esc(result['suggestion']) +
                        '</p><h4>' + tr('请求 / Request') + '</h4>' + code(result['request']) + '<h4>' + tr('响应 / Response') + '</h4>' + code(result['response']) +
                        '<h4>' + tr('逐项探测 / Attempts') + '</h4>' + code(json.dumps(result.get('attempts', []), ensure_ascii=False, indent=2)) +
                        '<h4>' + tr('载体支持证据 / Supporting checks') + '</h4>' + code(json.dumps(result.get('supporting_checks', []), ensure_ascii=False, indent=2)) + '</section>')
    summary = job['summary']
    totals = tr('通过 {passed} · 未通过 {failed} · 需复核 {review} · 未执行 {not_run}').format(**summary)
    headers = ''.join('<th>' + esc(tr(text)) + '</th>' for text in ('设备 ID', '设备名称', '接口', '验证范围', '结论', '故障类别', '耗时 ms'))
    title = esc(tr('工控设备验收报告'))
    return '<!doctype html><html lang="' + ('en' if lang == 'en' else 'zh-CN') + '''"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>''' + title + '''</title><style>
*{box-sizing:border-box}body{font:15px/1.65 system-ui,sans-serif;color:#183240;background:#eef3f5;margin:0;padding:32px}main{max-width:1120px;margin:auto;background:white;padding:38px;border-radius:16px}h1{font-size:30px;margin:8px 0}h2{border-bottom:1px solid #dce6e9;padding-bottom:8px;margin-top:32px}h3,h4{margin-bottom:6px}.brand{letter-spacing:2px;font-size:12px;color:#54717b}.notice{border-left:4px solid #bd770e;background:#fff6df;padding:14px}.conclusion{background:#edf7f5;padding:18px;font-size:18px}dl{display:grid;grid-template-columns:1fr 1fr;gap:16px}dt{font-size:12px;color:#54717b}dd{margin:0;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;border-bottom:1px solid #dce6e9;padding:10px;overflow-wrap:anywhere}th{background:#edf3f4}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f6f7;padding:12px;font-size:12px}.evidence{break-inside:avoid;border-bottom:1px solid #dce6e9;padding-bottom:16px}.muted{font-size:13px;color:#54717b}@media(max-width:640px){body{padding:10px}main{padding:20px}dl{grid-template-columns:1fr}}@media print{body{background:white;padding:0}main{max-width:none;padding:0}details{display:block}table{font-size:11px}.scroll{overflow:visible}h2{break-after:avoid}}
</style></head><body><main><div class="brand">QLDEVICECHECK / FIELD ACCEPTANCE</div>''' + '<h1>' + title + '</h1><p class="notice">' + esc(notice) + '</p><div class="conclusion"><strong>' + esc(labels[summary['verdict']]) + ' / ' + esc(summary['verdict']) + '</strong><br>' + esc(scope) + ': ' + str(selected) + ' / ' + str(expected) + ' ' + tr('台') + '<br><span class="muted">' + esc(totals) + '</span></div><p class="muted">' + tr('部分设备复测通过不表示整机恢复。任务中止、取消或失败时，已收集的证据仍保留，结论需复核。') + '</p><h2>' + tr('验收记录') + '</h2><dl>' + info + '</dl><h2>' + tr('设备结论') + '</h2><div class="scroll"><table><thead><tr>' + headers + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><h2>' + tr('测试证据') + '</h2>' + ''.join(evidence) + '<h2>' + tr('配置快照与任务事件') + '</h2><p class="muted">' + tr('SHA-256 用于识别配置版本；本报告未使用数字签名，不提供防篡改证明。') + '</p>' + code(json.dumps({'config_snapshot': job['config_snapshot'], 'events': job['events']}, ensure_ascii=False, indent=2)) + '</main></body></html>'


def export_job(job, format, lang='zh'):
    if lang not in ('zh', 'en'):
        raise ValueError('Unsupported report language')
    marker = 'SIMULATED' if job['mode'] == 'demo' else 'LIVE'
    if format == 'json':
        return json.dumps(dict(job, report_marker=marker), ensure_ascii=False, indent=2).encode(), 'application/json; charset=utf-8'
    if format == 'html':
        content = html_report(job, marker, lang=lang)
        return content.encode(), 'text/html; charset=utf-8'
    if format == 'csv':
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        columns = ['report_marker', 'job_id', 'status', 'mode', 'scenario', 'station_id', 'batch', 'operator', 'notes', 'config_hash', 'config_snapshot', 'created_at', 'finished_at', 'device_id', 'name', 'connection_type', 'interface', 'protocol', 'scope', 'verdict', 'fault_code', 'summary', 'suggestion', 'request', 'response', 'duration_ms', 'simulated', 'attempts', 'supporting_checks']
        writer.writerow(columns)
        def safe(value):
            text = str(value if value is not None else '')
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text
        for result in job['results']:
            row = dict(job, **job['metadata'], **result, report_marker=marker, job_id=job['id'], config_snapshot=json.dumps(job['config_snapshot'], ensure_ascii=False))
            for key in ('attempts', 'supporting_checks'):
                row[key] = json.dumps(result.get(key, []), ensure_ascii=False)
            writer.writerow([safe(row.get(key, '')) for key in columns])
        return stream.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8'
    raise ValueError('Unsupported export format')
