<div class="page-intro"><p>先搜索套餐用户，再查看其设备日志和问题分析。日志按已确认的设备归属关联。</p><a class="btn ghost" href="/admin/logs">全部设备日志</a></div>
<form class="filter-bar" method="get" action="/admin/customer-logs" role="search" aria-label="搜索套餐用户">
<label>搜索套餐用户<input type="search" name="user_q" value="<?= e($userKeyword) ?>" placeholder="邮箱、姓名或用户编号" maxlength="120"></label><button class="btn">搜索用户</button>
<?php if($userKeyword!==''): ?><a class="btn ghost" href="/admin/customer-logs">清除搜索</a><?php endif; ?>
</form>
<?php if(!$uid): ?>
<section class="card table-card"><div class="table-scroll"><table><thead><tr><th>用户编号</th><th>套餐用户</th><th>姓名</th><th>操作</th></tr></thead><tbody>
<?php foreach($userResults['rows'] as $item): ?><tr><td>#<?= (int)$item['id'] ?></td><td><a href="<?= e(lc_customer_log_url((int)$item['id'],['page'=>1,'q'=>'','err'=>'0','user_page'=>$userResults['page']])) ?>"><?= e($item['email']) ?></a></td><td><?= e($item['name'] ?: '未填写') ?></td><td><a class="btn ghost sm" href="<?= e(lc_customer_log_url((int)$item['id'],['page'=>1,'q'=>'','err'=>'0','user_page'=>$userResults['page']])) ?>">查看用户日志</a></td></tr><?php endforeach; ?>
<?php if(!$userResults['rows']): ?><tr><td colspan="4" class="empty">没有匹配的套餐用户，请尝试邮箱、姓名或用户编号。</td></tr><?php endif; ?>
</tbody></table></div></section>
<nav class="pager" aria-label="套餐用户搜索分页"><span>共 <?= (int)$userResults['total'] ?> 位用户，第 <?= (int)$userResults['page'] ?> / <?= (int)$userResults['pages'] ?> 页</span><?php foreach(['上一页'=>$userResults['page']-1,'下一页'=>$userResults['page']+1] as $label=>$n): if($n<1||$n>$userResults['pages'])continue; ?><a href="?<?= e(http_build_query(['user_q'=>$userKeyword,'user_page'=>$n])) ?>"><?= e($label) ?></a><?php endforeach; ?></nav>
<?php else: ?>
<section class="card"><div class="section-title"><div><span class="eyebrow">CUSTOMER LOGS</span><h2><?= e($user['name'] ?: $user['email']) ?></h2><p class="muted"><?= e($user['email']) ?> · 用户 #<?= (int)$uid ?> · 当前筛选 <?= (int)$total ?> 条日志</p></div><div class="actions"><a class="btn ghost" href="<?= e(lc_customer_log_url(0,['page'=>1,'q'=>'','err'=>'0'])) ?>">重新选择用户</a><a class="btn ghost" href="/admin/shop-users/<?= (int)$uid ?>">用户详情</a></div></div>
<form class="filter-bar" method="get" action="/admin/customer-logs">
<input type="hidden" name="user_id" value="<?= (int)$uid ?>"><input type="hidden" name="user_q" value="<?= e($userKeyword) ?>"><input type="hidden" name="user_page" value="<?= (int)$userResults['page'] ?>">
<label>搜索日志<input type="search" name="q" value="<?= e($keyword) ?>" placeholder="文件名、软件版本或设备 ID" maxlength="120"></label>
<label class="inline-check"><input type="checkbox" name="err" value="1" <?= $onlyErr?'checked':'' ?>>仅有错误</label><button class="btn">筛选日志</button>
</form></section>
<div class="card table-card"><div class="table-scroll"><table><thead><tr><th>上传时间</th><th>设备 ID</th><th>日志文件</th><th>软件版本</th><th>事件 / 错误</th><th>操作</th></tr></thead><tbody>
<?php foreach($rows as $r): $detail='/admin/customer-logs/'.$uid.'/'.(int)$r['id'].'?'.http_build_query(['user_q'=>$userKeyword,'user_page'=>$userResults['page'],'q'=>$keyword,'err'=>$onlyErr?'1':'0','page'=>$page]); ?><tr><td><?= e($r['created_at']) ?></td><td class="mono"><?= e($r['identity']) ?></td><td><a href="<?= e($detail) ?>"><?= e($r['filename']) ?></a></td><td><?= e($r['client_version'] ?: '未上报') ?></td><td><?= (int)$r['event_count'] ?> / <span class="<?= $r['error_count']?'text-danger':'' ?>"><?= (int)$r['error_count'] ?></span></td><td><a class="btn ghost sm" href="<?= e($detail) ?>">详情与诊断</a></td></tr><?php endforeach; ?>
<?php if(!$rows): ?><tr><td colspan="6" class="empty"><?= $keyword!==''||$onlyErr?'没有符合筛选条件的日志，可清空关键词或取消错误筛选。':'该用户尚无关联日志。请确认软件账号已绑定设备并成功上传；历史设备可在用户详情中核实关联。' ?></td></tr><?php endif; ?>
</tbody></table></div></div>
<nav class="pager" aria-label="日志分页"><span>共 <?= (int)$total ?> 条，第 <?= (int)$page ?> / <?= (int)$pages ?> 页</span><?php foreach(['上一页'=>$page-1,'下一页'=>$page+1] as $label=>$n): if($n<1||$n>$pages)continue; ?><a href="<?= e(lc_customer_log_url($uid,['page'=>$n])) ?>"><?= e($label) ?></a><?php endforeach; ?></nav>
<?php endif; ?>
