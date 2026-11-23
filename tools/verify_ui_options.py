"""遍历每个开关的两态、下拉框每个选项，记录配置变更；不冒充远端功能成功。"""
from pathlib import Path
from types import SimpleNamespace as NS
from copy import deepcopy
import json
from ui_sandbox import sandbox, build_app, walk, label
import flet as ft

def _page_platforms():
    """按平台实际页面生成遍历表（每个平台独立审计，页面号可重复）。"""
    from aeclone.platforms import PLATFORMS
    out = []
    for pid, pf in PLATFORMS.items():
        if not pf.implemented:
            continue
        for p in pf.pages:
            out.append((p.index, pid))
    return out


def main():
    results=[]
    page_platform = _page_platforms()
    for page, platform_id in sorted(page_platform):
        with sandbox():
            app=build_app()
            if platform_id != "alibaba":
                app._switch_platform(platform_id)
            app._switch(page)
            specs=[(i,label(c),[False,True] if isinstance(c,(ft.Switch,ft.Checkbox)) else
                    [o.key for o in c.options])
                   for i,c in enumerate(walk(app.content.controls))
                   if isinstance(c,(ft.Switch,ft.Checkbox,ft.Dropdown)) and
                   callable(c.on_change) and not c.disabled]
        for position,title,values in specs:
            with sandbox(external_calls=[]) as cfg:
                app=build_app()
                if platform_id != "alibaba":
                    app._switch_platform(platform_id)
                app._switch(page)
                control=list(walk(app.content.controls))[position]
                for value in values:
                    before=deepcopy(cfg.as_dict())
                    control.value=value
                    try:
                        control.on_change(NS(control=control,data=str(value),page=app.page))
                        after=cfg.as_dict()
                        changed=[k for k in after if before.get(k)!=after[k]]
                        # 防抖写盘在生产中由 Timer 完成；此处显式触发已安排的同一回调。
                        timer=getattr(app,'_autosave_timer',None)
                        if timer:
                            timer.cancel(); timer.function(*timer.args,**timer.kwargs)
                        persisted=[]
                        if cfg.path().exists():
                            disk=json.loads(cfg.path().read_text())
                            persisted=[k for k in changed if disk.get(k)==after[k]]
                        status='CONFIG_SAVED' if changed and len(persisted)==len(changed) else 'CONFIG_MEMORY' if changed else 'NO_CONFIG_CHANGE'
                        results.append(dict(page=page,platform=platform_id,label=title,
                                            value=value,status=status,changed=changed))
                    except Exception as ex:
                        results.append(dict(page=page,label=title,value=value,status='ERROR',detail=type(ex).__name__+': '+str(ex)[:180]))
    counts={s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}
    out=Path('docs/ui-options-verification.json')
    out.write_text(json.dumps(dict(counts=counts,results=results),ensure_ascii=False,indent=2))
    print(json.dumps(counts,ensure_ascii=False))
    for row in results:
        if row['status'] in ('ERROR','CONFIG_MEMORY'): print(json.dumps(row,ensure_ascii=False))
    return int(any(r['status']=='ERROR' for r in results))
if __name__=='__main__': raise SystemExit(main())
