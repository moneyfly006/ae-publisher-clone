<div class="card">
  <div class="section-title"><h2>当前生效规则</h2><a class="btn ghost" href="/admin/settings?section=logs">编辑识别规则</a></div>
  <table>
    <thead><tr><th >事件</th><th>关键词</th></tr></thead>
    <tbody>
    <?php foreach ($rules as $ev => $list): ?>
      <tr><td><span class="badge muted"><?= e($ev) ?></span></td><td><?= e(implode(' / ', $list)) ?></td></tr>
    <?php endforeach; ?>
    </tbody>
  </table>
</div>

<div class="card">
  <h2>未识别的可疑行（最近 10 份日志）</h2>
  <p class="hint">这些行包含“失败/异常/刊登/草稿/上传”等字样，但没有命中任何关键词。把它们补进「系统设置 → 日志与保留」即可。</p>
  <?php if (!$buckets): ?>
    <div class="empty">没有未识别的行，规则覆盖良好。</div>
  <?php else: ?>
    <table>
      <thead><tr><th >出现次数</th><th>样例</th></tr></thead>
      <tbody>
      <?php foreach ($buckets as $k => $b): ?>
        <tr><td><b><?= (int)$b['count'] ?></b></td><td style="word-break:break-all"><?= e($b['sample']) ?></td></tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  <?php endif; ?>
</div>
