<form class="analysis-filters" method="get">
<label>时间范围<select name="days"><?php foreach([1=>'今天',7=>'最近 7 天',30=>'最近 30 天',90=>'最近 90 天'] as $n=>$label): ?><option value="<?= $n ?>" <?= $filter['days']===$n?'selected':'' ?>><?= e($label) ?></option><?php endforeach; ?></select></label>
<label>平台<select name="platform"><option value="">全部平台</option><?php foreach($platforms as $p): ?><option value="<?= e($p['id']) ?>" <?= ($_GET['platform'] ?? '')===$p['id']?'selected':'' ?>><?= e($p['name']) ?></option><?php endforeach; ?><option value="unknown" <?= ($_GET['platform'] ?? '')==='unknown'?'selected':'' ?>>未识别平台</option></select></label>
<label>软件版本<select name="version"><option value="">全部版本</option><?php foreach($versions as $v): ?><option value="<?= e($v['version']) ?>" <?= ($_GET['version'] ?? '')===$v['version']?'selected':'' ?>><?= e($v['version']) ?></option><?php endforeach; ?></select></label>
<label>套餐用户<select name="user_id"><option value="">全部客户</option><?php foreach($users as $u): ?><option value="<?= (int)$u['id'] ?>" <?= $filter['uid']===(int)$u['id']?'selected':'' ?>><?= e($u['email']) ?></option><?php endforeach; ?></select></label>
<label>设备授权 ID<input name="identity" value="<?= e($_GET['identity'] ?? '') ?>" placeholder="精确设备 ID…"></label>
<label>日志关键词<input type="search" name="q" value="<?= e($_GET['q'] ?? '') ?>" placeholder="搜索错误信息…"></label>
<div class="filter-actions"><button class="btn">应用筛选</button><a class="btn ghost" href="<?= e(parse_url($_SERVER['REQUEST_URI'],PHP_URL_PATH)) ?>">重置</a></div>
</form>
