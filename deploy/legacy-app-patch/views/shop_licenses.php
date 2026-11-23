<div class="card" style="padding:8px 16px">
  <?php if (!$rows): ?><div class="empty">暂无套餐授权</div><?php else: ?>
  <table>
    <thead><tr><th>ID</th><th>用户</th><th>机器码</th><th>平台</th><th>到期</th><th>状态</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>
    <?php foreach ($rows as $r): $pl = json_decode((string)$r['platforms'], true) ?: []; ?>
      <tr>
        <td><?= (int)$r['id'] ?></td>
        <td><?= e($r['email'] ?? '') ?></td>
        <td class="mono"><?= e($r['machine_code'] ?: '未绑定') ?></td>
        <td><?= e(implode(',', array_keys($pl))) ?></td>
        <td><?= e($r['expires_at']) ?></td>
        <td><span class="badge <?= (int)$r['revoked'] ? 'bad' : 'ok' ?>"><?= (int)$r['revoked'] ? '已吊销' : '有效' ?></span></td>
        <td class="ops">
          <?php if (!(int)$r['revoked']): ?>
          <form method="post" action="/admin/shop-licenses/<?= (int)$r['id'] ?>/revoke" onsubmit="return confirm('吊销该授权？')">
            <?= csrf_field() ?><button class="btn ghost sm danger" type="submit">吊销</button>
          </form>
          <?php else: ?>—<?php endif; ?>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
  <?php endif; ?>
</div>
