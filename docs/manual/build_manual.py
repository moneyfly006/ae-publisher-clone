#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成手册三件套：打印版 HTML/PDF（含页眉页脚页码）、网页版、Word 版。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # ae-clone/
SHOTS = ROOT / "site" / "assets" / "shots"
OUT = ROOT / "docs" / "manual"
WEB = ROOT / "site" / "manual"
OUT.mkdir(parents=True, exist_ok=True)
WEB.mkdir(parents=True, exist_ok=True)
VERSION = "1.0.4"
QQ = "3219904322"

CHAPTERS = [
    ("0", "概览", "一屏掌握店铺状态，一键跑完一批商品。", "p0",
     [("状态数字", "采集箱 / 待发布 / 发布失败 / 在线 / 审核中；点数字可直接跳到对应页面"),
      ("启动", "按当前设置开始跑一批商品（默认只处理采集箱）"),
      ("关键词", "只处理标题包含该词的商品，留空 = 全部"),
      ("应用范围", "限定本次处理哪些商品"),
      ("清空", "清空关键词与范围"),
      ("搜索全部商品", "不受采集箱限制，按关键词搜索全店商品"),
      ("刷新数量", "重新拉取各状态数量"),
      ("准备状态", "Cookie / AI API / 模板 / 授权，绿色才能一键启动；红色点进去修"),
      ("今日任务", "成功 / 失败 / 今日花费"),
      ("成本 / 用量", "今日与近 7 天花费、按功能拆分、7 天柱状趋势"),
      ("最近处理", "只看今天 / 只看失败 / 搜索；点缩略图看大图")],
     "第一次用先看「准备状态」，四项全绿再点启动。"),
    ("1", "店铺与模板", "连接店小秘、选店铺、选模板商品。", "p1",
     [("读取并同步", "自动读取本机 Chrome/Edge 已登录的店小秘并同步店铺"),
      ("重新检测 Cookie", "Cookie 过期时点它重新读取"),
      ("粘贴 Cookie", "读不到时手动粘贴一次"),
      ("选择店铺", "切换要操作的店铺，下方显示在线/待发布/审核中数量"),
      ("模板来源", "在线 / 采集箱 / 本地模板"),
      ("类目来源", "自动分类（商品自带类目）或按模板"),
      ("类目映射 / 学习", "把采集类目映射到你的常用类目"),
      ("选择模板产品", "选一个模板商品，后续继承运费模板、属性、重量尺寸"),
      ("保存模板到本地", "把模板参数存到本地，断网也能用"),
      ("模板库", "管理多个模板"),
      ("读取模板参数", "读取类目、运费、属性数量、重量，并显示模板首图")],
     "模板商品决定类目/运费/属性；标题、SKU、图片仍按新商品生成。"),
    ("2", "类目与属性", "类目、规格库存、属性默认值与 SKU 图替换。", "p2",
     [("类目", "显示当前生效方式；点「去店铺设置切换」改来源"),
      ("是否原箱", "按原箱计算运费与重量"),
      ("重量/长宽高", "默认规格，模板缺省时使用"),
      ("货值默认值", "申报价值，报关用"),
      ("高关注化学品 / 是否带电", "类目属性兜底，模板读不到时用这里"),
      ("属性模板与 SKU 图替换", "折叠区：搜索属性模板、清空已应用属性；把某个 SKU 图换成指定 URL"),
      ("欧盟资质", "需要时再填"),
      ("套装 SKU", "可选：为主 SKU 追加套装 SKU")],
     "属性冲突、重复属性会在执行前被检查出来，不用等平台报错。"),
    ("3", "AI 文案", "英文标题、五点描述、SKU 名称。", "p3",
     [("生成范围", "标题 / 描述 / SKU 名称，可单独勾选"),
      ("提示词模板", "选择或编辑提示词，支持 {title} 等占位"),
      ("生成预览", "先预览再写入，避免直接改坏商品"),
      ("应用", "写回当前商品或本次批量"),
      ("模型", "固定使用配置好的文本模型，无需自选")],
     "建议先用 1 个商品预演，确认风格后再批量。"),
    ("4", "内容审查", "违禁词检测与替换。", "p4",
     [("检测范围", "标题 / 属性 / SKU / 详情"),
      ("词库", "内置违禁词 + 自定义词"),
      ("开始检测", "扫描当前商品或本次批量"),
      ("结果", "标出命中的词与位置，可替换或忽略")],
     "上架前跑一次，能减少平台下架风险。"),
    ("5", "AI 图片", "白底、去水印、翻译、精细化编辑。", "p5",
     [("简单编辑 / 精细化编辑", "两种模式切换"),
      ("常用预设", "只做白底 / 只去水印 / 只做翻译 / 全部关闭，点后先预览再执行"),
      ("主图白底", "开关 + 起始/结束序号，只处理指定张"),
      ("主图去水印/Logo", "独立开关与序号范围"),
      ("主图文字翻译", "翻译序号内的文字"),
      ("描述图 / SKU 图", "同一套开关，分别控制"),
      ("精细化编辑", "逐张任务：第几张 + 提示词 + 参考图 + 插入位置"),
      ("冲突检查", "执行前提示互相冲突的开关")],
     "白底/去水印约 ¥0.1/张；重复图片命中缓存与幂等键，不会重复扣费。"),
    ("6", "价格", "倍率、区域定价、多 SKU 算价。", "p6",
     [("价格模式", "倍率 / 固定加价 / 成本利润"),
      ("倍率 / 汇率", "按倍率换算售价，支持区域倍率"),
      ("区域定价", "按国家/地区单独定价"),
      ("多 SKU 分别计算", "不同 SKU 可用不同价格与重量"),
      ("试算预览", "改完先看结果再保存"),
      ("平台上限", "超过 999999 会自动截断并提示")],
     "价格改完先试算，再批量跑，避免全店返工。"),
    ("7", "变种与库存", "SKU 组合、库存、重量尺寸、编码回写。", "p7",
     [("SKU 组合", "选择属性生成组合"),
      ("库存", "批量设置库存"),
      ("重量尺寸", "每个 SKU 可单独设置"),
      ("SKU 编码", "自动生成规则；可回写到商品"),
      ("套装 SKU", "追加套装组合"),
      ("同步到商品", "把当前设置写入商品")],
     "多 SKU 商品若被「SKU 编码写回」卡住，可先关闭该开关再跑。"),
    ("8", "发布队列", "待发布 / 发布失败 / 发布中，集中发布与重试。", "p8",
     [("待发布 / 发布失败 / 发布中", "三个数字，点开看对应列表"),
      ("刷新数量", "重新统计"),
      ("看待发布列表", "弹窗：按图数量排序，可多选发布"),
      ("看发布失败列表", "失败原因在列表与日志里"),
      ("批量发布", "二次确认后批量提交"),
      ("重试", "只重试失败的商品"),
      ("单条发布", "列表里每行右侧「发布」，成功后从列表移除")],
     "发布前确认商品在采集箱；待发布/在线商品不能再用 API 直存。"),
    ("9", "图片工作台", "单商品手动调试图片。", "p9",
     [("① 单张处理", "白底 / 去水印 / 翻译 / 编辑"),
      ("② 组合与输出", "四合一、合成视频、上传、导出"),
      ("③ 维护", "清理旧图、清理缓存"),
      ("图片墙", "排序、只看有图、点图放大"),
      ("处理结果", "每张结果可点「对比」看处理前后"),
      ("任务队列", "进度条与待处理/完成/失败统计")],
     "调试图效果用这一页，不会写回商品。"),
    ("10", "任务与日志", "实时日志、筛选、导出。", "p10",
     [("停止任务", "当前商品结束后停止"),
      ("日志级别", "全部 / INFO / WARN / ERROR"),
      ("搜索", "按产品 ID 或关键词"),
      ("清空筛选", "恢复全部日志"),
      ("跟随最新", "自动滚到最新一条"),
      ("导出筛选结果 / 导出 CSV", "导出当前筛选，CSV 可直接用 Excel 打开"),
      ("复制全部日志", "整段复制给客服排查"),
      ("清空日志", "二次确认后清空本地日志"),
      ("上传运行日志", "把最近日志上报授权后台，方便远程排查")],
     "出问题先按产品 ID 筛选，把对应几行发出来最快。"),
    ("11", "设置", "授权、API、图床、界面与诊断。", "p11",
     [("机器码 / 复制 / 重新检测", "换机或授权异常时用"),
      ("授权状态", "到期时间与是否有效"),
      ("API 地址与 Key", "中转站配置"),
      ("推荐配置", "一键恢复图床 / API 默认值"),
      ("图床", "图片转存地址与 Key"),
      ("模型与价格", "当前模型与单价（只读）"),
      ("界面与运行", "启动页面、窗口大小、快捷键"),
      ("系统诊断", "API / 浏览器 / 店小秘 / ffmpeg / 磁盘；缺 ffmpeg 可一键下载")],
     "换电脑：复制机器码发给管理员，授权可迁移。"),
]

FAQS = [
    ("启动没反应 / 提示配置冲突", "看概览「准备状态」：Cookie、AI API、模板、授权四项全绿才能启动；红色项点进去修复。也可点「检查配置冲突」。"),
    ("保存失败：只有采集箱商品才能操作", "商品已不在采集箱（待发布/审核中/在线）。先在店小秘移回采集箱，或直接去「发布队列」处理。"),
    ("图片没变化", "确认「AI 图片」里对应开关已开、序号包含该张；精细化编辑要有提示词。日志会写明「模型」「超时」或「尺寸不合格」。"),
    ("提示未找到 ffmpeg", "只影响「生成轮播视频」。到设置 → 系统诊断点「下载并安装 ffmpeg」，或关掉视频来源里的「合成视频」。"),
    ("重复扣费吗", "不会。图片请求带幂等键，超时重试、手动重跑同一张图都只计费一次。"),
    ("换电脑授权还在吗", "授权绑定机器码。把新机器码发给管理员迁移，或在授权后台解绑旧机器。"),
    ("想回滚某次改动", "软件保存前会保留原商品；图片/文案修改前可先预演，批量前建议只跑 1 个确认。"),
    ("怎么反馈问题最快", "任务与日志 → 按产品 ID 筛选 → 复制日志（或导出 CSV）发给客服，附上软件版本与操作步骤。"),
    ("只想处理其中几个商品", "概览填「关键词」只跑标题包含该词的；或用「应用范围」限定；也可以直接去「发布队列」按列表处理。"),
    ("图片尺寸不达标被丢弃", "日志会写「尺寸不合格」。可在设置里调整最小尺寸要求，或让 AI 重新生成；营销图由本地裁切自动补足 800×800。"),
    ("多店铺怎么切换", "店铺与模板 → 选择店铺，切换后重新读取模板参数；每个店铺可保存各自的模板与价格策略。"),
    ("配置和日志在哪", "Windows：%LOCALAPPDATA%\\AuroraList；macOS：~/Library/Application Support/AuroraList。备份 config.json 即可迁移配置。"),
]

QUICK = [
    ("01", "装好并授权", "打开软件 → 设置页确认「授权有效」。换机时把机器码发给管理员迁移。"),
    ("02", "连店小秘", "店铺与模板 → 读取并同步；Cookie 过期就「重新检测」或「粘贴 Cookie」。"),
    ("03", "选模板并读取", "选一个模板商品 → 读取模板参数，确认类目、运费、属性、重量都正确。"),
    ("04", "配文案与图片", "AI 文案选提示词；AI 图片按需开白底/去水印/翻译，先只处理 1-3 张看效果。"),
    ("05", "先跑 1 个，再批量", "概览 → 关键词填一个商品 → 启动。确认无误后再「搜索全部商品」批量跑。"),
]

esc = lambda t: (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------- 打印版（含页眉页脚页码） ----------------
def print_html() -> str:
    total = 3 + len(CHAPTERS) + 1
    pages = []

    pages.append(f"""
<section class="page cover">
  <div class="cover-glow"></div>
  <img class="logo" src="../../site/assets/logo.png" alt=""/>
  <div class="eyebrow">AURORALIST · USER MANUAL</div>
  <h1>极光上架<br/>操作手册</h1>
  <p class="sub">从装好软件到跑完第一批商品 · v{VERSION}</p>
  <div class="cover-meta"><span>12 个功能页面</span><span>逐按钮说明</span><span>Windows / macOS</span></div>
  <div class="cover-foot">咨询 QQ {QQ} · 官方下载页 ali.moneyfly.club</div>
</section>""")

    def head(title):
        return (f'<div class="p-head"><span class="ph-brand">极光上架 AuroraList</span>'
                f'<span class="ph-title">{esc(title)}</span>'
                f'<span class="ph-ver">v{VERSION}</span></div>')

    def foot(no):
        return (f'<div class="p-foot"><span>咨询 QQ {QQ}</span>'
                f'<span class="pf-mid">极光上架 · 操作手册</span>'
                f'<span>第 {no} / {total} 页</span></div>')

    toc = "".join(f'<li><b>{num:0>2}</b><span>{esc(t)}</span><i>{esc(d)}</i></li>'
                  for num, t, d, *_ in CHAPTERS)
    pages.append(f"""
<section class="page">
  {head("目录")}
  <div class="body">
    <h2 class="pt">目录</h2>
    <div class="toc-wrap">
      <ol class="toc">{toc}</ol>
      <div class="toc-side">
        <h3>建议阅读顺序</h3>
        <p>1 · 先看「新手 5 步」把第一批商品跑通<br/>2 · 再按页面查按钮作用<br/>3 · 出错时看「常见问题」</p>
        <div class="note">本手册对应 v{VERSION}；升级后按钮位置如有微调，以软件内实际为准。</div>
      </div>
    </div>
  </div>
  {foot(2)}
</section>""")

    steps = "".join(f'<div class="qs-step"><b>{n}</b><div><h3>{esc(t)}</h3><p>{esc(d)}</p></div></div>' for n, t, d in QUICK)
    pages.append(f"""
<section class="page">
  {head("新手 5 步，跑通第一批")}
  <div class="body">
    <h2 class="pt">新手 5 步，跑通第一批</h2>
    <div class="qs">
      <div class="qs-steps">{steps}
        <div class="mini-shot"><img src="../../site/assets/shots/p0.png" alt=""/><span>概览 · 准备状态全绿再启动</span></div>
      </div>
      <div class="qs-side">
        <div class="box ok"><h3>启动前，4 项必须全绿</h3><ul>
          <li>Cookie：已连接店小秘</li><li>AI API：模型可用（预检通过）</li>
          <li>模板商品：已选并读取参数</li><li>授权：有效且未到期</li></ul>
          <p class="mini">概览「准备状态」里是红色就先修，别急着点启动。</p></div>
        <div class="box"><h3>跑完后，检查这 5 项</h3><ul>
          <li>标题：英文通顺、无违禁词</li><li>主图：白底/去水印符合预期</li>
          <li>价格：利润与区域价正确</li><li>属性：类目/必填项齐全</li>
          <li>状态：店小秘「待发布」能看到</li></ul></div>
        <div class="box warn"><h3>建议节奏</h3><p class="mini">第 1 天：跑 1 个商品；第 2 天：跑 10 个；确认稳定后再整批跑。</p></div>
      </div>
    </div>
  </div>
  {foot(3)}
</section>""")

    for i, (num, title, desc, img, items, tip) in enumerate(CHAPTERS):
        lis = "".join(f'<li><b>{k+1:02d}</b><div><strong>{esc(a)}</strong><span>{esc(b)}</span></div></li>'
                      for k, (a, b) in enumerate(items))
        pages.append(f"""
<section class="page chapter">
  {head(esc(title))}
  <div class="body">
    <div class="chap-head"><div class="chap-no">{num}</div>
      <div><h2 class="pt">{esc(title)}</h2><p class="desc">{esc(desc)}</p></div></div>
    <div class="chap-body">
      <div class="shot"><img src="../../site/assets/shots/{img}.png" alt=""/></div>
      <ol class="legend">{lis}</ol>
    </div>
    <div class="tipbar">提示 · {esc(tip)}</div>
  </div>
  {foot(4 + i)}
</section>""")

    faq = "".join(f'<div class="q"><h3>{esc(q)}</h3><p>{esc(a)}</p></div>' for q, a in FAQS)
    pages.append(f"""
<section class="page">
  {head("常见问题与排查")}
  <div class="body">
    <h2 class="pt">常见问题与排查</h2>
    <div class="faq">{faq}</div>
    <div class="tipbar">快捷键：⌘R / Ctrl+R 刷新 · ⌘L 打开日志 · ⌘F 全局搜索商品</div>
  </div>
  {foot(total)}
</section>""")

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@600;700;900&family=Noto+Sans+SC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/>
<style>
  @page {{ size: A4 landscape; margin: 0; }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:"Noto Sans SC",sans-serif;color:#101828;background:#fff;-webkit-print-color-adjust:exact}}
  .page{{width:297mm;height:210mm;page-break-after:always;position:relative;overflow:hidden;background:#fff}}
  .body{{position:absolute;left:14mm;right:14mm;top:26mm;bottom:20mm}}
  .p-head{{position:absolute;left:14mm;right:14mm;top:9mm;display:flex;align-items:baseline;
    gap:4mm;font-size:8pt;color:#8A93A8;border-bottom:.4pt solid #E3E7EF;padding-bottom:2mm}}
  .ph-brand{{font-weight:700;color:#101828}}
  .ph-title{{color:#5B6478}}
  .ph-ver{{margin-left:auto;font-family:"JetBrains Mono",monospace;font-size:7pt;letter-spacing:.12em}}
  .p-foot{{position:absolute;left:14mm;right:14mm;bottom:8mm;display:flex;align-items:center;
    font-size:7.6pt;color:#8A93A8;border-top:.4pt solid #E3E7EF;padding-top:2mm}}
  .pf-mid{{margin:0 auto}}
  .eyebrow{{font-family:"JetBrains Mono",monospace;font-size:7.5pt;letter-spacing:.22em;color:#0F9E7F}}
  .pt{{font-family:"Noto Serif SC",serif;font-size:21pt;font-weight:900;letter-spacing:-.01em}}
  .desc{{color:#5B6478;font-size:9.5pt;margin-top:1.5mm}}
  .cover{{background:#07080D;color:#F3F5FB;padding:22mm}}
  .cover-glow{{position:absolute;width:120mm;height:90mm;left:-30mm;top:-30mm;border-radius:50%;filter:blur(45px);background:#7C5CFF;opacity:.55}}
  .cover-glow::after{{content:"";position:absolute;left:150mm;top:40mm;width:100mm;height:80mm;border-radius:50%;background:#00D2A8;opacity:.35;filter:blur(45px)}}
  .cover .logo{{position:relative;width:18mm;height:18mm;border-radius:5mm;box-shadow:0 6mm 16mm rgba(124,92,255,.4)}}
  .cover .eyebrow{{position:relative;color:#00D2A8;margin-top:6mm}}
  .cover h1{{position:relative;font-family:"Noto Serif SC",serif;font-size:46pt;line-height:1.12;font-weight:900;margin-top:4mm}}
  .cover .sub{{position:relative;color:#98A0B8;font-size:12pt;margin-top:5mm}}
  .cover-meta{{position:relative;display:flex;gap:6mm;margin-top:12mm}}
  .cover-meta span{{font-family:"JetBrains Mono",monospace;font-size:8pt;letter-spacing:.1em;color:#C9CEE0;
    border:1px solid rgba(255,255,255,.18);border-radius:99px;padding:2mm 4mm}}
  .cover-foot{{position:absolute;left:22mm;right:22mm;bottom:16mm;color:#8B92A8;font-size:9pt;
    border-top:1px solid rgba(255,255,255,.14);padding-top:4mm}}
  .toc-wrap{{display:grid;grid-template-columns:1.5fr 1fr;gap:10mm;margin-top:6mm}}
  .toc{{list-style:none;display:grid;grid-auto-flow:column;grid-template-rows:repeat(6,auto);grid-template-columns:1fr 1fr;gap:2.2mm 10mm}}
  .toc li{{display:flex;align-items:baseline;gap:2.5mm;border-bottom:1px dotted #D8DCE6;padding-bottom:1.6mm;font-size:9pt}}
  .toc b{{font-family:"JetBrains Mono",monospace;font-size:8pt;color:#7C5CFF;width:7mm}}
  .toc span{{font-weight:700;white-space:nowrap}}
  .toc i{{font-style:normal;color:#8A93A8;font-size:8pt;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .toc-side h3{{font-family:"Noto Serif SC",serif;font-size:13pt;margin-bottom:3mm}}
  .toc-side p{{font-size:9pt;color:#5B6478;line-height:1.9}}
  .note{{margin-top:5mm;font-size:8pt;color:#8A93A8;background:#F4F6FA;border-radius:3mm;padding:4mm}}
  .chap-head{{display:flex;gap:5mm;align-items:flex-start}}
  .chap-no{{font-family:"JetBrains Mono",monospace;font-size:26pt;font-weight:600;color:#7C5CFF;line-height:1}}
  .chap-body{{display:grid;grid-template-columns:1.78fr 1fr;gap:8mm;margin-top:4mm;align-items:start}}
  .shot{{border-radius:3mm;overflow:hidden;border:1px solid #E3E7EF;box-shadow:0 6mm 14mm rgba(16,24,40,.10)}}
  .shot img{{width:100%;display:block}}
  .legend{{list-style:none;display:flex;flex-direction:column;gap:1.8mm}}
  .legend li{{display:flex;gap:2.6mm;font-size:8.7pt;line-height:1.45}}
  .legend b{{font-family:"JetBrains Mono",monospace;font-size:7pt;color:#fff;background:#7C5CFF;
    width:5.4mm;height:5.4mm;border-radius:2mm;display:grid;place-items:center;flex:none;margin-top:.4mm}}
  .legend strong{{display:block;color:#101828}}
  .legend span{{color:#5B6478}}
  .tipbar{{position:absolute;left:0;right:0;bottom:0;font-size:8.4pt;color:#0F5132;
    background:#EAF7F1;border-left:3px solid #00A97F;border-radius:2mm;padding:3mm 4mm}}
  .qs{{display:grid;grid-template-columns:1.15fr .85fr;gap:8mm;margin-top:5mm}}
  .qs-steps{{display:flex;flex-direction:column;gap:2mm}}
  .qs-step{{display:flex;gap:3.5mm;background:#F7F8FC;border:1px solid #E6EAF3;border-radius:3.5mm;padding:3mm 4mm}}
  .qs-step b{{font-family:"JetBrains Mono",monospace;font-size:13pt;color:#7C5CFF;line-height:1}}
  .qs-step h3{{font-size:10.5pt;margin-bottom:1mm}}
  .qs-step p{{font-size:7.9pt;color:#5B6478;line-height:1.5}}
  .qs-side{{display:flex;flex-direction:column;gap:3mm}}
  .mini-shot{{margin-top:2mm;border:1px solid #E6EAF3;border-radius:3mm;overflow:hidden;position:relative;box-shadow:0 3mm 8mm rgba(16,24,40,.08)}}
  .mini-shot img{{width:100%;height:40mm;object-fit:cover;object-position:top;display:block}}
  .mini-shot span{{position:absolute;left:3mm;bottom:2.5mm;font-size:7pt;color:#fff;background:rgba(8,10,16,.62);padding:1mm 2.4mm;border-radius:2mm}}
  .box{{border:1px solid #E6EAF3;border-radius:3.5mm;padding:4mm 5mm;background:#fff}}
  .box.ok{{background:#F1FBF7;border-color:#BFE9DA}}
  .box.warn{{background:#FFF8EC;border-color:#F3DDB8}}
  .box h3{{font-size:10pt;margin-bottom:2mm}}
  .box ul{{list-style:none;display:flex;flex-direction:column;gap:1.4mm;font-size:8.3pt;color:#3C465C}}
  .box li::before{{content:"✓";color:#0F9E7F;font-weight:700;margin-right:2mm}}
  .mini{{font-size:7.8pt;color:#6B7488;line-height:1.6;margin-top:2mm}}
  .faq{{display:grid;grid-template-columns:1fr 1fr;gap:3mm 6mm;margin-top:4mm}}
  .q{{border:1px solid #E6EAF3;border-radius:3mm;padding:3.4mm}}
  .q h3{{font-size:9.2pt;margin-bottom:1.2mm}}
  .q p{{font-size:7.7pt;color:#5B6478;line-height:1.55}}
</style></head><body>
{''.join(pages)}
</body></html>"""


# ---------------- 网页版 ----------------
def web_html() -> str:
    navs = "".join(f'<a href="#c{num}">{num:0>2} {esc(t)}</a>' for num, t, *_ in CHAPTERS)
    secs = []
    for num, title, desc, img, items, tip in CHAPTERS:
        legend = "".join(f'<li><b>{i+1:02d}</b><div><strong>{esc(a)}</strong><span>{esc(b)}</span></div></li>'
                         for i, (a, b) in enumerate(items))
        secs.append(f"""
<section class="sec" id="c{num}">
  <div class="sec-head"><span class="no">{num:0>2}</span>
    <div><h2>{esc(title)}</h2><p>{esc(desc)}</p></div></div>
  <div class="sec-body">
    <figure><a href="../assets/shots/{img}.png" target="_blank"><img src="../assets/shots/{img}.png" alt="{esc(title)} 界面"/></a>
      <figcaption>点击图片查看原图 · 界面截图 v{VERSION}</figcaption></figure>
    <ol class="legend">{legend}</ol>
  </div>
  <div class="tip">提示 · {esc(tip)}</div>
</section>""")

    quick = "".join(f'<div class="qs"><b>{n}</b><div><h3>{esc(t)}</h3><p>{esc(d)}</p></div></div>' for n, t, d in QUICK)
    faq = "".join(f'<div class="q"><h3>{esc(q)}</h3><p>{esc(a)}</p></div>' for q, a in FAQS)

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>极光上架 AuroraList · 操作手册 v{VERSION}</title>
<link rel="icon" href="../assets/logo.png"/>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@600;700;900&family=Noto+Sans+SC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/>
<style>
  :root{{--bg:#07080D;--card:rgba(255,255,255,.04);--line:rgba(255,255,255,.1);--ink:#F3F5FB;--muted:#98A0B8;--dim:#6B7288;--v:#7C5CFF;--t:#00D2A8}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);font-family:"Noto Sans SC",sans-serif;line-height:1.75;overflow-x:hidden}}
  img{{max-width:100%}}
  a{{color:inherit;text-decoration:none}}
  .bg{{position:fixed;inset:0;pointer-events:none;overflow:hidden}}
  .glow{{position:absolute;border-radius:50%;filter:blur(90px);opacity:.4}}
  .g1{{width:700px;height:500px;left:-160px;top:-180px;background:#7C5CFF}}
  .g2{{width:600px;height:440px;right:-200px;top:240px;background:#00D2A8;opacity:.25}}
  .top{{position:sticky;top:0;z-index:20;backdrop-filter:blur(16px);background:rgba(7,8,13,.82);border-bottom:1px solid var(--line)}}
  .top-in{{max-width:1200px;margin:0 auto;padding:14px 24px;display:flex;align-items:center;gap:14px}}
  .top img{{width:32px;height:32px;border-radius:9px}}
  .top b{{font-weight:700}}
  .top small{{display:block;font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:.18em;color:var(--muted)}}
  .top .r{{margin-left:auto;display:flex;gap:10px;flex-wrap:wrap}}
  .btn{{padding:8px 16px;border-radius:999px;font-size:13px;border:1px solid var(--line);color:var(--muted)}}
  .btn.pri{{background:linear-gradient(120deg,#8B6BFF,#00D2A8);color:#07080D;border:none;font-weight:700}}
  .layout{{max-width:1200px;margin:0 auto;padding:36px 24px 90px;display:grid;grid-template-columns:220px 1fr;gap:36px;align-items:start}}
  .side{{position:sticky;top:84px;border:1px solid var(--line);border-radius:16px;padding:16px;background:var(--card)}}
  .side h3{{font-size:12px;letter-spacing:.16em;color:var(--dim);font-family:"JetBrains Mono",monospace;margin-bottom:10px}}
  .side a{{display:block;padding:7px 8px;border-radius:8px;font-size:13px;color:var(--muted)}}
  .side a:hover{{background:rgba(124,92,255,.14);color:#fff}}
  .hero{{border:1px solid var(--line);border-radius:22px;padding:34px;background:radial-gradient(120% 140% at 10% 0%,rgba(124,92,255,.22),transparent 60%),var(--card);margin-bottom:26px}}
  .eyebrow{{font-family:"JetBrains Mono",monospace;font-size:11px;letter-spacing:.2em;color:var(--t)}}
  h1{{font-family:"Noto Serif SC",serif;font-size:clamp(30px,4vw,46px);line-height:1.2;margin:10px 0 10px;font-weight:900}}
  .hero p{{color:var(--muted);font-size:14.5px}}
  .quick{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:18px 0 8px}}
  .qs{{border:1px solid var(--line);border-radius:14px;padding:16px;background:var(--card)}}
  .qs b{{font-family:"JetBrains Mono",monospace;color:var(--v);font-size:15px}}
  .qs h3{{font-size:14px;margin:6px 0 4px}}
  .qs p{{font-size:12.5px;color:var(--muted);line-height:1.6}}
  .sec{{border:1px solid var(--line);border-radius:20px;padding:26px;margin-bottom:20px;background:var(--card);scroll-margin-top:80px}}
  .sec-head{{display:flex;gap:14px;align-items:flex-start;margin-bottom:16px}}
  .no{{font-family:"JetBrains Mono",monospace;font-size:26px;color:var(--v);line-height:1}}
  .sec h2{{font-family:"Noto Serif SC",serif;font-size:24px;font-weight:900}}
  .sec-head p{{color:var(--muted);font-size:13px;margin-top:2px}}
  .sec-body{{display:grid;grid-template-columns:1.5fr 1fr;gap:20px;align-items:start}}
  figure img{{width:100%;border-radius:12px;border:1px solid var(--line);box-shadow:0 18px 50px rgba(0,0,0,.5)}}
  figcaption{{font-size:11.5px;color:var(--dim);margin-top:6px;font-family:"JetBrains Mono",monospace}}
  .legend{{list-style:none;display:flex;flex-direction:column;gap:8px}}
  .legend li{{display:flex;gap:10px;font-size:12.8px;line-height:1.6}}
  .legend b{{font-family:"JetBrains Mono",monospace;font-size:10px;color:#07080D;background:linear-gradient(120deg,#8B6BFF,#00D2A8);
    width:22px;height:22px;border-radius:7px;display:grid;place-items:center;flex:none;margin-top:2px;font-weight:700}}
  .legend strong{{display:block;color:#fff;font-weight:500}}
  .legend span{{color:var(--muted)}}
  .tip{{margin-top:14px;font-size:12.5px;color:#BFF3E3;background:rgba(0,210,168,.09);
    border-left:3px solid var(--t);border-radius:8px;padding:10px 14px}}
  .faq{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  .q{{border:1px solid var(--line);border-radius:12px;padding:14px;background:rgba(255,255,255,.02)}}
  .q h3{{font-size:13.5px;margin-bottom:4px}}
  .q p{{font-size:12.5px;color:var(--muted);line-height:1.6}}
  footer{{border-top:1px solid var(--line);padding:26px 24px;text-align:center;color:var(--dim);font-size:12.5px}}
  @media(max-width:900px){{.layout{{grid-template-columns:1fr}}.side{{display:none}}.sec-body{{grid-template-columns:1fr}}.faq{{grid-template-columns:1fr}}}}
  @media(max-width:560px){{
    .top-in{{padding:10px 12px;gap:8px}}
    .top b{{font-size:13.5px}}
    .top small{{font-size:9px;letter-spacing:.1em}}
    .top .r{{gap:6px}}
    .btn{{padding:6px 10px;font-size:11.5px}}
    .layout{{padding:18px 12px 60px;gap:18px}}
    .hero{{padding:20px;border-radius:16px}}
    .sec{{padding:16px;border-radius:16px}}
    .sec h2{{font-size:20px}}
    h1{{font-size:26px}}
    .quick{{grid-template-columns:1fr}}
    .top-in{{flex-wrap:wrap}}
    .top .r{{margin-left:0;width:100%;justify-content:flex-start}}
    .top .r a:first-child{{display:none}}
    .layout{{display:block}}
    .hero,.sec{{max-width:100%}}
    .hero p{{font-size:13px}}
  }}
</style></head><body>
<div class="bg"><div class="glow g1"></div><div class="glow g2"></div></div>
<header class="top"><div class="top-in">
  <img src="../assets/logo.png" alt=""/><div><b>极光上架 AuroraList</b><small>USER MANUAL</small></div>
  <div class="r"><a class="btn" href="../">← 返回官网</a><a class="btn pri" href="#download">下载 v{VERSION}</a></div>
</div></header>

<div class="layout">
  <aside class="side"><h3>目录</h3><a href="#quickstart">新手 5 步</a>{navs}<a href="#faq">常见问题</a></aside>
  <main>
    <section class="hero">
      <div class="eyebrow">操作手册 · v{VERSION}</div>
      <h1>从装好软件，<br/>到跑完第一批商品</h1>
      <p>每个页面、每个按钮做什么，都写在这里。遇到问题先看「常见问题与排查」。</p>
      <div class="quick" id="quickstart">{quick}</div>
    </section>
    {''.join(secs)}
    <section class="sec" id="faq"><div class="sec-head"><span class="no">?</span>
      <div><h2>常见问题与排查</h2><p>客服最常被问到的 12 个问题</p></div></div>
      <div class="faq">{faq}</div>
      <div class="tip">快捷键：⌘R / Ctrl+R 刷新 · ⌘L 打开日志 · ⌘F 全局搜索商品</div>
    </section>
    <section class="sec" id="download"><div class="sec-head"><span class="no">↓</span>
      <div><h2>下载与咨询</h2><p>Windows / macOS 安装包，免费更新</p></div></div>
      <div class="quick">
        <a class="qs" href="https://github.com/moneyfly006/ae-publisher-clone/releases/download/v{VERSION}/AuroraList-Setup-{VERSION}.exe">
          <h3>Windows 安装包</h3><p>Setup · 约 48 MB</p></a>
        <a class="qs" href="https://github.com/moneyfly006/ae-publisher-clone/releases/download/v{VERSION}/AuroraList-macos-arm64.zip">
          <h3>macOS（Apple 芯片）</h3><p>Zip · 约 71 MB</p></a>
        <a class="qs" href="https://github.com/moneyfly006/ae-publisher-clone/releases/download/v{VERSION}/AuroraList-macos-x64.zip">
          <h3>macOS（Intel）</h3><p>Zip · 约 72 MB</p></a>
        <a class="qs" href="manual.pdf"><h3>PDF 版手册</h3><p>离线查看 / 打印</p></a>
        <a class="qs" href="manual.docx"><h3>Word 版手册</h3><p>可编辑，方便改字</p></a>
        <div class="qs"><h3>咨询 QQ</h3><p>{QQ}</p></div>
      </div>
    </section>
  </main>
</div>
<footer>极光上架 AuroraList · v{VERSION} · 咨询 QQ {QQ} · https://ali.moneyfly.club</footer>
</body></html>"""


def build_docx():
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    for s in doc.sections:
        s.top_margin = Cm(2.0); s.bottom_margin = Cm(2.0)
        s.left_margin = Cm(2.0); s.right_margin = Cm(2.0)

    def title(text, size=26, color=(16, 24, 40)):
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r = p.add_run(text); r.bold = True; r.font.size = Pt(size)
        r.font.color.rgb = RGBColor(*color); r.font.name = "Microsoft YaHei"
        return p

    def para(text, size=10.5, color=(91, 100, 120), space_after=6):
        p = doc.add_paragraph()
        r = p.add_run(text); r.font.size = Pt(size)
        r.font.color.rgb = RGBColor(*color); r.font.name = "Microsoft YaHei"
        p.paragraph_format.space_after = Pt(space_after)
        return p

    title("极光上架 AuroraList", 30)
    title(f"操作手册 v{VERSION}", 20, (124, 92, 255))
    para("从装好软件到跑完第一批商品 · 12 个功能页面逐按钮说明")
    para(f"咨询 QQ {QQ}   ·   官方下载页 ali.moneyfly.club", 9.5)
    doc.add_page_break()

    title("新手 5 步，跑通第一批", 18)
    for n, t, d in QUICK:
        p = doc.add_paragraph(style="List Number")
        r = p.add_run(f"{t}："); r.bold = True; r.font.size = Pt(11)
        r2 = p.add_run(d); r2.font.size = Pt(10.5)
    para("启动前 4 项必须全绿：Cookie、AI API、模板商品、授权。")
    para("跑完检查 5 项：标题、主图、价格、属性、店小秘「待发布」里能看到。")
    doc.add_page_break()

    for num, t, d, img, items, tip in CHAPTERS:
        title(f"{num:0>2}  {t}", 18)
        para(d)
        pic = SHOTS / f"{img}.png"
        if pic.exists():
            doc.add_picture(str(pic), width=Cm(16.5))
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "编号"; hdr[1].text = "按钮 / 区域"; hdr[2].text = "作用"
        for i, (a, b) in enumerate(items, 1):
            row = table.add_row().cells
            row[0].text = f"{i:02d}"; row[1].text = a; row[2].text = b
        for row in table.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9.5)
        p = doc.add_paragraph()
        r = p.add_run("提示 · " + tip); r.font.size = Pt(10)
        r.font.color.rgb = RGBColor(15, 81, 50)
        doc.add_page_break()

    title("常见问题与排查", 18)
    for q, a in FAQS:
        p = doc.add_paragraph()
        r = p.add_run(q); r.bold = True; r.font.size = Pt(11)
        para(a, 10, space_after=10)
    para("快捷键：⌘R / Ctrl+R 刷新 · ⌘L 打开日志 · ⌘F 全局搜索商品", 9.5)

    out = OUT / f"AuroraList-操作手册-v{VERSION}.docx"
    doc.save(out)
    return out


if __name__ == "__main__":
    p_html = print_html()
    p_path = OUT / "manual-print.html"
    p_path.write_text(p_html, encoding="utf-8")
    (WEB / "index.html").write_text(web_html(), encoding="utf-8")
    docx = build_docx()
    print("print:", p_path)
    print("web:", WEB / "index.html")
    print("docx:", docx)
