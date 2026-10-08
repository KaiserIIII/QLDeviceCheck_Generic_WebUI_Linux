'use strict';
// Only explicit interface phrases are translated. User/configuration/evidence data
// never passes through this dictionary or a document-wide text replacement.
globalThis.QLI18N = (() => {
  const en = {
    'QL DeviceCheck · 现场验收工作台':'QL DeviceCheck · Field acceptance workbench',
    '工控机设备检测':'Industrial device diagnostics',
    '界面语言':'Interface language', '工位工作台':'STATION WORKBENCH',
    '主导航':'Main navigation','工位总览':'Station overview','新建验收':'New inspection',
    '设备清单':'Device catalog','任务历史':'Task history','配置校验':'Configuration validation',
    '▦　工位总览':'▦　Station overview','＋　新建验收':'＋　New inspection',
    '◫　设备清单':'◫　Device catalog','◷　任务历史':'◷　Task history','≋　配置校验':'≋　Configuration validation',
    '连接中':'Connecting','应检设备 · 协议证据 · 复测追溯':'Expected devices · Protocol evidence · Retest history',
    '验收中心 / ':'Acceptance center / ','访问凭证':'Access credentials',
    '现场记录保存在当前工位 · v3.1':'Records stay on this station · v3.1',
    '设备测试证据':'Device test evidence','关闭证据':'Close evidence',
    '连接受保护的工位':'Connect to a protected station',
    '令牌仅用于本次浏览器会话，不写入报告。':'The token is used only for this browser session and is excluded from reports.',
    '访问令牌':'Access token','取消':'Cancel','保存并连接':'Save and connect',
    '访问令牌（仅保存在当前浏览器会话）':'Access token (stored only for this browser session)',
    '扫描设备':'Scan devices','测试全部':'Test all','保存':'Save','等待扫描':'Waiting for scan',
    '串':'SER','网':'NET','报':'RPT','串口':'Serial','网口':'Network','测试报告':'Test report',
    '0 个设备':'0 devices','尚未扫描':'Not scanned yet','尚未生成报告':'No report yet',
    '正在读取工位数据…':'Loading station data…',
    '等待执行':'Queued','执行中':'Running','正在取消':'Cancelling','已完成':'Completed',
    '已取消':'Cancelled','执行故障':'Failed','已中断':'Interrupted',
    '通过':'Pass','失败':'Fail','待确认':'Review required','未执行':'Not run',
    '全部正常':'All healthy','混合故障':'Mixed faults','响应超时':'Response timeout',
    'CRC 错误':'CRC error','设备缺失':'Missing device','通信路径验证':'Communication path verification',
    'PCI 被动检查':'Passive PCI inspection','CRC 校验失败':'CRC validation failed',
    '应检设备未响应':'Expected device did not respond','载体通信失败':'Carrier communication failed',
    '响应不匹配':'Response mismatch','接口或设备不可用':'Interface or device unavailable','尚未执行':'Not run yet',
    '请求失败 (':'Request failed (','FIELD ACCEPTANCE / 工位验收':'FIELD ACCEPTANCE',
    'SIMULATED · 当前为离线演示，使用合成设备与报文，不连接现场硬件。':'SIMULATED · Offline demo with synthetic devices and messages. No field hardware is connected.',
    '工位 / 批次':'Station / batch','任务 / 创建时间':'Task / created','执行状态':'Execution status',
    '验收结果':'Inspection verdict','操作':'Actions','未命名工位':'Unnamed station','无批次':'No batch',
    '未记录操作者':'Operator not recorded',' 通过':' passed','查看任务 →':'View task →','任务详情':'Task details',
    '正在读取工位数据':'Loading station data','暂时无法读取工位':'Station unavailable',
    '检查服务和访问凭证后重试。':'Check the service and access credentials, then retry.',
    '让每一次验收，有据可查。':'Make every inspection traceable.',
    '从应检清单到协议证据，再到交付后的复测对比。所有统计来自当前工位保存的任务记录。':'From the expected device list to protocol evidence and retest comparisons after delivery. All statistics come from task records saved on this station.',
    '先演示一轮故障，再验证恢复。':'Demonstrate faults, then verify recovery.',
    '新一轮验收，从完整设备清单开始。':'Start the next inspection with the complete device list.',
    '配置中应存在的设备都会进入验收结果。未响应设备保留失败记录，复测后可检查恢复、退化与证据变化。':'Every configured device appears in the results. Devices that do not respond retain a failed result. Retest to check recovery, regression and changes in evidence.',
    '开始验收 ↗':'Start inspection ↗','应检设备':'Expected devices',
    '当前配置中的完整清单':'Complete list in the current configuration','已完成任务':'Completed tasks',
    '当前模式的已完成验收':'Completed inspections in this mode','通过任务':'Passed tasks',
    '所有选定设备均通过':'All selected devices passed','失败任务':'Failed tasks',
    '至少一项设备验收失败':'At least one device failed','最近验收':'Recent inspections',
    '全部任务 →':'All tasks →','等待第一份验收记录':'Waiting for the first inspection record',
    '创建任务后，这里会展示工位、批次和结果。':'Create a task to see its station, batch and results here.',
    '验收流程':'Inspection workflow','完整清单':'Complete device list',
    '未发现、超时或无响应设备也保留结果。':'Results include missing devices, timeouts and devices that do not respond.',
    '可追溯证据':'Traceable evidence','接口、请求和响应与本次配置快照一起保存。':'Interfaces, requests and responses are saved with the configuration snapshot.',
    '复测与比较':'Retest and compare','检查配置和范围，区分部分复测与整套验收。':'Check configuration and scope to distinguish a partial retest from a whole-unit inspection.',
    '设备组成':'Device composition','按配置统计':'From configuration','串口 / RS-485':'Serial / RS-485',
    '网络 / TCP · UDP':'Network / TCP · UDP','PCI / 被动状态':'PCI / passive status',
    '通信响应证明当前通信路径可用；PCI 被动状态不代表完整业务功能或电气动作正常。':'A response verifies the current communication path. Passive PCI status does not establish full application functionality or electrical operation.',
    '查看应检清单':'View expected devices','当前配置':'Current configuration',
    '任务创建时固定配置快照，历史结果不会随配置修改而改变。':'Each task retains its configuration snapshot. Later configuration changes do not alter historical results.',
    '新建现场验收':'New field inspection','记录工位与批次，按当前配置检查全部应检设备。':'Record the station and batch, then inspect all expected devices in the current configuration.',
    '验收信息':'Inspection information','工位 / 设备编号':'Station / equipment ID',
    'FAT-01 / 设备序列号':'FAT-01 / equipment serial number','批次编号':'Batch ID','操作者':'Operator',
    '验收工程师':'Inspection engineer','演示场景':'Demo scenario','验收备注':'Inspection notes',
    '现场环境、变更或本次验收目的':'Site conditions, changes or inspection purpose',
    '同一工位同一时间执行一个任务。':'One task runs on this station at a time.',
    '创建验收任务':'Create inspection task','本次应检':'Devices in this inspection',
    '未响应设备保留为失败项；取消后未执行的设备单独标记。':'Devices that do not respond retain failed results. Devices skipped after cancellation are marked separately.',
    '应检设备清单':'Expected device catalog',
    '这份清单决定验收覆盖范围。设备无需先被扫描发现，才进入验收结果。':'This catalog defines inspection coverage. Devices appear in the results even if a scan has not discovered them.',
    '新建验收任务':'New inspection task',' 个应检设备':' expected devices','设备 / 标识':'Device / ID',
    '接口类型':'Interface type','接口 / 槽位':'Interface / slot','协议':'Protocol','验收范围':'Inspection scope',
    '按配置匹配':'Match configuration','被动检查':'Passive inspection','配置摘要 ':'Configuration hash ',
    '打开原有设备扫描与单接口检查界面 →':'Open legacy device scanning and interface diagnostics →',
    '按工位、批次或操作者查找验收记录，选择两次任务查看恢复与退化。':'Find records by station, batch or operator. Compare two tasks to inspect recovery and regression.',
    '搜索记录':'Search records','工位 / 批次 / 操作者':'Station / batch / operator','全部状态':'All statuses',
    '时间排序':'Time order','最新在前':'Newest first','最早在前':'Oldest first','筛选':'Filter',
    '未找到任务':'No tasks found','调整筛选条件或创建新任务。':'Adjust the filters or create a task.',
    '共 ':'Total: ',' 个任务':' tasks','上一页':'Previous page','下一页':'Next page',
    '两次任务比较':'Compare two tasks','基线任务':'Baseline task','当前任务':'Current task',
    '选择任务':'Select a task','比较任务':'Compare tasks','未命名':'Unnamed',
    '校验设备配置':'Validate device configuration',
    '检查设备标识、接口参数和只读验收规则。校验不会修改工位当前配置。':'Check device IDs, interface parameters and read-only inspection rules. Validation does not change the station configuration.',
    '配置内容':'Configuration content','设备配置 JSON':'Device configuration JSON',
    '载入当前配置':'Load current configuration','校验配置':'Validate configuration','正在读取任务':'Loading task',
    '验收任务':'Inspection task','任务 ':'Task ','指定设备复测':'Selected-device retest','设备验收':'Device inspection',
    '返回历史':'Back to history','取消任务':'Cancel task','SIMULATED / 合成验收记录':'SIMULATED / SYNTHETIC RECORD',
    'LIVE / 现场验收记录':'LIVE / FIELD RECORD','复测验收':'Retest inspection','工位验收':'Station inspection',
    '操作者 ':'Operator: ','未记录':'Not recorded','创建 ':'Created: ','执行 ':'Duration: ',
    '设备进度 ':'Device progress: ','本次配置摘要 ':'Task configuration hash ',
    '本任务只复测原任务未通过设备。部分复测通过不代表整套设备验收通过。':'This task retests only devices that did not pass the original task. A passed partial retest does not establish that the whole unit passed.',
    '本次选定的验收范围':'Selected scope for this task','保留对应通信 / 被动证据':'Communication / passive evidence retained',
    '失败 / 待确认':'Failed / review required','需要排查或现场确认':'Investigation or site confirmation needed',
    '等待执行或提前结束':'Waiting or ended before execution','设备验收结果':'Device inspection results',
    '本次范围 ':'Task scope: ','设备':'Device','接口 / 协议':'Interface / protocol','判定依据':'Decision basis',
    '耗时':'Duration','查看证据':'View evidence','复测与交付':'Retest and delivery','复测场景':'Retest scenario',
    '复测未通过设备':'Retest devices that did not pass',
    '已完成当前选定范围检查，证据与配置快照已保留。':'The selected scope is complete. Evidence and the configuration snapshot are retained.',
    '执行结束后可复测或导出。取消在当前设备组结束后生效。':'Retest or export when execution ends. Cancellation takes effect after the current device group finishes.',
    '与原任务比较':'Compare with original task','导出 ':'Export ',
    '报告保留验收范围、报文证据与判定。模拟报告始终标记 SIMULATED。':'Reports retain scope, protocol evidence and verdicts. Demo reports always carry the SIMULATED marker.',
    '任务记录':'Task log','全部探测记录 (':'All probe records (','载体检查证据 (':'Carrier check evidence (',
    '载体检查支持本次设备判定，不扩大选定设备范围。':'Carrier checks support this device verdict without expanding the selected scope.',
    '设备标识':'Device ID','接口':'Interface','请求 / 检查方式':'Request / inspection method',
    '无报文 / 被动检查':'No message / passive inspection','响应 / 采集结果':'Response / collected result',
    '无响应记录':'No response recorded','排查建议':'Troubleshooting guidance',
    '基于规则的建议，需要现场确认。':'Rule-based guidance requires confirmation on site.',
    '此项结果只覆盖 ':'This result covers only ','，不覆盖电气动作或完整业务功能。':'; it does not cover electrical operation or full application functionality.',
    '复测结果比较':'Retest comparison','不满足可比条件：':'Comparison conditions not met: ',
    '。以下差异仅供查看。':'. Differences below are observations only.',
    '部分范围比较：只检查共同设备，未宣称整套设备已恢复。':'Partial-scope comparison: only common devices are checked; whole-unit recovery is not established.',
    '模式、配置和选定设备范围一致，可比较验收状态。':'Mode, configuration and selected scope match. Inspection states are comparable.',
    '恢复':'Recovered','退化':'Regressed','仍失败':'Still failed','未变化':'Unchanged','观察到的':'Observed: ',
    '新增':'Added','未纳入本次复测':'Outside this retest','移除':'Removed','其他变化':'Other changes',
    '报告导出失败':'Report export failed','请选择两个不同任务':'Select two different tasks',
    '离线演示工位':'Offline demo station','本地现场工位':'Local field station',
    '连接工位后开始验收':'Connect to the station to start inspections',
    '服务不可用或需要访问凭证，请通过右上角输入令牌。':'The service is unavailable or requires credentials. Enter an access token using the control above.',
    '操作失败':'Operation failed','报告地址无效':'Invalid report address','报告读取失败 (':'Report read failed (',
    ')，请检查访问凭证':'). Check the access credentials','已连接':'Connected','正常':'Healthy','异常':'Fault',
    '等待测试':'Waiting for test','连通正常':'Communication verified','无响应':'No response','接口不存在':'Interface missing',
    '测试失败':'Test failed',' · 经':' · via ','可用':'Available','未确定接口':'Unspecified interface',
    '已发现已知设备':'Known device discovered',' 个设备':' devices','未发现接口':'No interfaces found',
    '未发现已知设备':'No known devices found','测试':'Test','通信方式':'Communication method','状态':'Status',
    '点击“测试全部”后生成报告':'Select “Test all” to generate a report','通过 ':'Passed: ','，失败 ':'; failed: ',
    '网页':'HTML','，发现 ':'; found ','测试中':'Testing','正在测试设备连通性':'Testing device communication',
    '设备连通正常':'Device communication verified','设备测试失败':'Device test failed','扫描中':'Scanning',
    '正在扫描接口 ':'Scanning interface ',' 扫描完成，发现 ':' scan complete; found ',
    '接口扫描失败':'Interface scan failed','访问令牌已保存，可继续扫描':'Access token saved. Scanning can continue.',
    '正在扫描已知设备':'Scanning known devices','扫描完成':'Scan complete',
    '正在扫描并测试全部已发现设备':'Scanning and testing all discovered devices','全部测试完成':'All tests complete',
    '报告读取失败':'Report read failed'
  };
  let language;
  try { language=localStorage.getItem('qldc.lang'); } catch (_) {}
  if (!['en','zh'].includes(language)) language=/^zh\b/i.test(globalThis.navigator?.language||'en')?'zh':'en';
  const listeners=new Set();
  const zh={
    'Missing station/unit identity':'缺少工位 / 设备标识','Station/unit identity mismatch':'工位 / 设备标识不一致',
    'Batch mismatch':'批次不一致','Mode mismatch':'模式不一致','Configuration mismatch':'配置不一致',
    'Selected scope mismatch':'选定范围不一致','Result scope mismatch':'结果范围不一致','Jobs are not finished':'任务尚未结束',
    'Acceptance queued':'验收任务已排队','Acceptance started':'验收任务已开始',
    'Acceptance completed':'验收任务已完成','Acceptance cancelled':'验收任务已取消',
    'Acceptance failed':'验收执行故障','Acceptance interrupted':'验收任务已中断',
    'Service restarted; no automatic hardware retry':'服务已重启；不会自动重试硬件操作',
    'Cancellation requested; effective at a device/group boundary':'已请求取消；在当前设备 / 设备组结束后生效',
    'Access token required':'需要访问令牌','Invalid access token':'访问令牌无效',
    'Legacy hardware operations are unavailable in demo mode':'演示模式不提供旧版硬件操作'
  };
  const t=phrase=>language==='en'?(en[phrase]??phrase):(zh[phrase]??phrase);
  function apply(root=document) {
    if (root===document) {
      document.documentElement.lang=language==='zh'?'zh-CN':'en';
      const title=document.documentElement.dataset.i18nTitle;
      if(title)document.title=t(title);
    }
    for(const [attribute,target] of [['data-i18n',null],['data-i18n-aria','aria-label'],['data-i18n-placeholder','placeholder']]) {
      root.querySelectorAll(`[${attribute}]`).forEach(element=>{
        const text=t(element.getAttribute(attribute));
        if(target)element.setAttribute(target,text);else element.textContent=text;
      });
    }
    const control=root.querySelector('#language-switch');if(control)control.value=language;
  }
  function setLanguage(value) {
    if(!['en','zh'].includes(value))return;
    language=value;
    try {localStorage.setItem('qldc.lang',value);} catch (_) {}
    apply();listeners.forEach(listener=>listener(value));
  }
  // Getters keep status/scenario label maps current without changing their IDs.
  function translatedLabels(map) {
    const result={};
    for(const [key,value] of Object.entries(map))Object.defineProperty(result,key,{enumerable:true,get:()=>t(value)});
    return result;
  }
  return {get language(){return language;},t,labels:translatedLabels,apply,setLanguage,onChange:callback=>listeners.add(callback)};
})();
