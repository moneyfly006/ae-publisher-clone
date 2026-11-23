<div class="toolbar">
  <div class="toolbar-right">
    <a class="btn ghost" href="/admin/shop-export?type=orders">导出订单 CSV</a>
  </div>
</div>
<div class="analysis-kpis financial-kpis compact">
  <div class="card"><div class="hint">累计收入</div><div>¥<?= number_format($d['total']/100,2) ?></div></div>
  <div class="card"><div class="hint">本月收入</div><div>¥<?= number_format($d['month']/100,2) ?></div></div>
  <div class="card"><div class="hint">已支付订单</div><div><?= number_format($d['orders']) ?> 笔</div></div>
</div>
<div class="card"><h3>近 6 个月收入</h3>
  <table><tr><th>月份</th><th>收入</th><th>占比</th></tr>
  <?php $max=max(1, max(array_column($d['months'],'cents'))); foreach ($d['months'] as $m): ?>
    <tr><td><?= e($m['label']) ?></td><td>¥<?= number_format($m['cents']/100,2) ?></td>
      <td><div style="height:10px;background:#DFF2EE;border-radius:5px"><div style="height:10px;width:<?= (int)($m['cents']/$max*100) ?>%;background:#0F766E;border-radius:5px"></div></div></td></tr>
  <?php endforeach; ?></table>
</div>
<div class="card"><h3>套餐销售</h3>
  <table><tr><th>套餐</th><th>订单数</th><th>收入</th></tr>
  <?php foreach ($d['by_pkg'] as $p): ?><tr><td><?= e($p['name'] ?? '已删除套餐') ?></td><td><?= (int)$p['n'] ?></td><td>¥<?= number_format($p['cents']/100,2) ?></td></tr><?php endforeach; ?>
  </table>
</div>
