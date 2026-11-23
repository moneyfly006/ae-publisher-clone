<?php $act=$mode==='create'?'/admin/licenses':'/admin/licenses/'.(int)$row['id'].'/update'; ?>
<?php if($errors): ?><div class="flash bad" role="alert"><?php foreach($errors as $error): ?><div><?= e($error) ?></div><?php endforeach; ?></div><?php endif; ?>
<section class="card"><h2>设备授权与有效期</h2><form method="post" action="<?= e($act) ?>"><?= csrf_field() ?><div class="form-grid">
<label class="field-wide">设备授权码<input id="identity" type="text" name="identity" value="<?= e($row['identity']) ?>" placeholder="粘贴软件显示的设备授权码…" required><span class="hint">从软件“设置 → 验证与授权”中复制并原样填写。原授权码验证规则保持不变。</span></label>
<label>客户端类型<select id="client_type" name="client_type"><option value="local" <?= ($row['client_type'] ?? 'local')==='local'?'selected':'' ?>>本地验证模式（设备授权）</option><option value="shop" <?= ($row['client_type'] ?? '')==='shop'?'selected':'' ?>>店铺验证模式</option></select></label>
<label>授权套餐名称<input id="plan" type="text" name="plan" value="<?= e($row['plan']) ?>" placeholder="例如：standard、月卡、年卡…"></label>
<label>到期日期<input id="expires_at" type="date" name="expires_at" value="<?= e($row['expires_at']) ?>" required></label>
<label>授权状态<select id="status" name="status"><?php foreach(['active'=>'正常','disabled'=>'停用','revoked'=>'吊销'] as $key=>$label): ?><option value="<?= e($key) ?>" <?= ($row['status'] ?? 'active')===$key?'selected':'' ?>><?= e($label) ?></option><?php endforeach; ?></select></label>
<label class="field-wide">备注<textarea id="note" name="note" rows="4" placeholder="客户名称、订单号或其他管理备注…"><?= e($row['note']) ?></textarea></label>
</div><div class="form-actions"><button class="btn" type="submit">保存设备授权</button><a class="btn ghost" href="<?= $mode==='create'?'/admin/licenses':'/admin/licenses/'.(int)$row['id'] ?>">取消</a></div></form></section>
