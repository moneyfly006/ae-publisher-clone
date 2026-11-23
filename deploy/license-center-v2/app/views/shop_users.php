<div class="page-intro"><p>按账号管理平台权限、设备和软件日志。原设备客户继续在「原设备授权」中管理。</p><a class="btn ghost" href="/admin/licenses">原设备授权</a></div>
<form class="filter-bar" method="get"><label>搜索账号 / 称呼<input type="search" name="q" value="<?= e($keyword) ?>" placeholder="输入邮箱或称呼…"></label><button class="btn">搜索用户</button><span class="muted">共 <?= (int)$total ?> 位用户</span></form>
<div class="card">
  <?php if (!$rows): ?><div class="empty">暂无用户</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>邮箱</th><th>称呼</th><th>有效平台</th><th>最近权限变更</th><th>已付订单</th><th>标签</th><th>注册时间</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): ?>
      <tr>
        <td><?= (int)$r['id'] ?></td>
        <td><?= e($r['email']) ?></td>
        <td><?= e($r['name'] ?? '') ?></td>
        <td><?= (int)$r['lic_count'] ?></td>
        <td><?= e($r['effective']['next_change_at'] ?? '无有效权限') ?></td>
        <td><?= (int)$r['paid_orders'] ?></td>
        <td><?= e($r['tags'] ?? '') ?></td>
        <td><?= e(date('Y-m-d', (int)$r['created_at'])) ?></td>
        <td class="ops"><a class="btn ghost sm" href="/admin/shop-users/<?= (int)$r['id'] ?>">管理</a>
          <a class="btn sm" href="/admin/shop-users/<?= (int)$r['id'] ?>#grant">追加权限</a>
<a class="btn ghost sm" href="/admin/customer-logs?user_id=<?= (int)$r['id'] ?>">日志</a></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
<nav class="pager" aria-label="用户分页"><span>第 <?= (int)$page ?> / <?= (int)$pages ?> 页</span><?php foreach(['上一页'=>$page-1,'下一页'=>$page+1] as $label=>$n): if($n<1||$n>$pages)continue; ?><a href="?<?= e(http_build_query(['q'=>$keyword,'page'=>$n])) ?>"><?= e($label) ?></a><?php endforeach; ?></nav>
