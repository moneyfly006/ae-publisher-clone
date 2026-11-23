<?php
declare(strict_types=1);
require_once __DIR__.'/diagnostics.php';

/** Local evidence-based triage. No external AI service, credentials or writes. */
function lc_log_diagnosis_rules(): array {return [
 'tls'=>['连接证书或 TLS 握手失败','高','证书链、代理 HTTPS 检查或系统 TLS 环境可能阻止了安全连接。',['检查系统时间、软件版本及系统根证书是否正常。','分别使用当前代理和可用的直连网络复测，核对代理是否替换 HTTPS 证书；修复证书信任，不关闭证书校验。'],'同一网络再次连接原服务，确认不再出现证书或握手错误。','/certificate verify failed|sslerror|tls handshake|could not find a suitable tls|CERTIFICATE_VERIFY_FAILED/iu'],
 'quota'=>['AI 或服务账户额度不足','高','服务商拒绝了调用，通常与余额、套餐额度或账户预算限制有关。',['在对应服务商后台核对账户余额、模型权限与预算，不以软件本地预估替代实际额度。','恢复额度后先运行一个样本；已扣费请求核对结果后再重试。'],'单次调用返回有效结果，不再出现 insufficient_quota 或额度不足。','/insufficient_quota|not enough quota|quota exceeded|insufficient balance|余额不足|额度不足|额度耗尽|预算超限/iu'],
 'rate_limit'=>['平台或服务商请求频率受限','中','请求速率超过服务商限制；并发任务、自动重试或账户限额可能共同触发。',['查看响应中的 Retry-After 和服务商频率限制。','降低并发与调用频率，采用有上限的退避重试；提交超时的商品先核对平台结果。'],'冷却后用单个商品重试，确认没有 429，且未出现重复提交。','/(?:HTTP|status(?:_code)?|error)[\s:=]*429\b|too many requests|rate.?limit(?:ed| exceeded)|请求过于频繁|频率限制/iu'],
 'category'=>['目标类目或店铺发布资格不匹配','高','店铺可能没有目标类目的发布权限，或所选类目与当前平台规则不匹配。',['在对应店铺和站点核对目标类目与商品类型。','确认类目准入、品牌资质及平台审批；有权限后再重新读取模板并填写属性。'],'在平台原编辑页面使用同一店铺与类目校验通过，再重试单件商品。','/cat_pub_restrict|category.*(?:denied|permission|restricted|invalid)|类目.*(?:准入|无权限|受限|不允许|错误)|无.*类目.*权限/iu'],
 'auth'=>['登录凭据或访问权限失效','高','当前 Cookie、Token、会话或接口权限可能已失效；403 还可能由平台访问限制触发。',['检查发生问题的平台及当前账号，更新对应 Cookie 或重新登录软件账号。','确认设备授权、店铺和站点归属，核对 401/403 响应正文；不要把凭据发到日志或工单中。'],'使用同一账号读取店铺或商品信息成功后，再运行单件任务。','/unauthorized|session expired|token expired|invalid token|登录失效|未登录|授权失效|cookie.*(?:失效|过期|invalid|expired)|(?:HTTP|status(?:_code)?)[\s:=]*(?:401|403)\b/iu'],
 'runtime'=>['软件执行发生程序异常','高','异常类型或堆栈指向软件处理分支；需结合触发操作与版本定位，不能仅靠重复运行解决。',['保留软件版本、发生时间、操作步骤与完整异常堆栈。','检查是否为旧版本问题；在隔离样本复现后，由开发者修复对应代码并做回归验证。'],'相同步骤在修复版本中完成，日志不再出现同类堆栈，且商品结果符合预期。','/Traceback \(most recent call last\)|(?:TypeError|AttributeError|KeyError|IndexError|NameError|UnboundLocalError|SyntaxError|Fatal error)\s*:/iu'],
 'price'=>['价格、物流或包装必填数据缺失','高','平台对价格、货值、运费或包装数据的校验未通过。',['定位响应中缺失的具体字段，核对币种、单位、重量和包装尺寸。','根据商品真实成本和物流方案补齐数据，再运行保存前校验。'],'平台字段校验通过，保存草稿后价格、币种和物流信息正确。','/货值不能为空|物流属性不能为空|CHK_BASIC_MAXVALUE|CHK_PLUG|(?:price|价格|运费|重量|包装).*(?:required|invalid|missing|不能为空|无效|错误)/iu'],
 'attribute'=>['商品必填属性或变体不符合模板','高','目标类目存在缺失、无效枚举或不兼容的变体主题。',['重新加载当前站点和类目属性模板，核对报错字段及允许值。','按商品真实资料补齐必填属性和变体关系，不自动编造品牌、认证或规格。'],'原平台编辑页的字段校验通过，再保存单个草稿检查变体和属性。','/variation_theme|attribute.*(?:required|missing|invalid)|missing.*attribute|必填属性|属性不能为空|属性.*(?:缺失|无效)/iu'],
 'identifier'=>['商品标识或品牌信息被平台拒绝','高','GTIN、UPC、ASIN、品牌或目录匹配可能与实际商品不一致。',['核对平台返回的标识错误、品牌备案和目录匹配结果。','使用真实有效的商品编码，或按平台流程申请编码豁免；不要生成随机 UPC 绕过校验。'],'原平台接受商品标识和品牌组合，草稿校验通过。','/(?:gtin|upc|asin|brand|品牌).*(?:invalid|mismatch|conflict|not found|无效|不一致|冲突|错误)/iu'],
 'image'=>['图片下载、上传或规格校验失败','中','图片地址、访问权限、尺寸或格式可能不符合目标平台要求。',['检查报错图片地址的可访问性、有效期、格式和尺寸。','确认上传图床成功，并按该平台真实要求调整图片后重新校验。'],'用同一图片在目标平台预览正常，上传响应成功且不再报错。','/(?:image|图片|营销图|主图).*(?:failed|invalid|timeout|timed out|cannot|失败|异常|不合格|无法|尺寸错误)|(?:download|upload).*image.*error/iu'],
 'ai_output'=>['AI 返回内容无法解析或不符合要求','中','模型输出格式、字段或长度与软件所需结构不一致，或服务返回了非预期内容。',['核对所用提示词、模型以及要求的输出格式，查看解析错误附近内容。','约束输出为所需字段和合法格式；涉及商品事实的内容由真实资料提供。'],'单次生成结果能被软件解析，字段齐全并通过平台校验。','/(?:AI|model|模型|文案).*(?:invalid json|parse error|解析失败|格式错误|字段缺失)|JSONDecodeError\s*:/iu'],
 'network'=>['网络连接超时或远端服务不可用','中','网络、代理或远端接口响应异常；提交超时本身不能证明平台没有收到请求。',['先核对对应商品在店小秘及目标平台的草稿、审核和发布状态，避免重复刊登。','检查代理、DNS 和服务可用性，适当调整超时；读取请求可有限重试，写入请求核实结果后再处理。'],'用单件商品复测连接，确认最终保存或发布状态，且没有重复商品。','/timed out|timeout|connection (?:reset|refused|aborted)|name resolution|网络.*(?:异常|失败)|连接.*(?:失败|超时)|请求超时|(?:HTTP|status(?:_code)?)[\s:=]*5\d\d\b/iu'],
];}
function lc_assess_log(array $log): array {
    $lines=preg_split('/\R/u',(string)($log['content'] ?? '')) ?: [];$total=count($lines);$offset=max(0,$total-8000);
    $rules=lc_log_diagnosis_rules();$issues=[];$failed=0;$draft=false;$submitted=false;
    foreach(array_slice($lines,$offset) as $n=>$line){
        $line=trim($line);if($line==='')continue;
        $draft=$draft||preg_match('/保存草稿|存入草稿|已保存草稿/u',$line)===1;
        $submitted=$submitted||preg_match('/产品已提交发布|刊登成功|发布成功/u',$line)===1;
        $kind='';
        foreach($rules as $id=>$rule)if(preg_match($rule[5],$line)===1){$kind=$id;break;}
        if($kind===''&&preg_match('/\[(?:ERROR|CRITICAL|FATAL)\]| - (?:ERROR|CRITICAL) - |(?:Exception|Error)\s*:|失败|异常/iu',$line))$kind='other';
        if($kind==='')continue;$failed++;
        if(!isset($issues[$kind])){
            $rule=$rules[$kind] ?? ['存在尚未归类的失败记录','待核实','当前日志提供了失败信号，但信息不足以确定根因。',['保留报错前后完整日志、软件版本、具体店铺和商品信息。','根据原平台错误响应核实必填项及权限；没有明确原因前避免批量重复提交。'],'补齐响应和复现步骤后，运行单件样本验证。'];
            $issues[$kind]=['type'=>$kind,'title'=>$rule[0],'priority'=>$rule[1],'cause'=>$rule[2],'steps'=>$rule[3],'verify'=>$rule[4],'count'=>0,'evidence'=>[]];
        }
        $issues[$kind]['count']++;
        if(count($issues[$kind]['evidence'])<3)$issues[$kind]['evidence'][]=['line'=>$offset+$n+1,'text'=>lc_redact($line,600)];
    }
    uasort($issues,function($a,$b){$r=['高'=>0,'中'=>1,'待核实'=>2];return ($r[$a['priority']]<=>$r[$b['priority']]) ?: ($b['count']<=>$a['count']);});
    if($issues){$status='needs_attention';$summary='记录到 '.count($issues).' 类需要排查的问题';$next='先处理高优先级问题，再用单件商品复测；下列判断基于本份日志，不能证明故障当前仍在发生。';}
    elseif((int)($log['error_count'] ?? 0)>0){$status='unknown';$summary='上传摘要有错误记录，当前文本不足以定位原因';$next='错误可能来自结构化上报或分析范围之外；请补充发生时间、完整响应及对应任务日志。';}
    elseif($draft){$status='draft';$summary='检测到保存草稿记录，尚不能确认已发布';$next='在店小秘草稿箱和目标平台核对商品状态；需要刊登时，补齐必填项并完成发布流程。';}
    elseif($submitted){$status='submitted';$summary='检测到提交或成功记录，需核对平台最终状态';$next='确认目标平台商品 ID、审核结果和在售状态；软件日志中的成功记录不等于最终上架结果。';}
    else{$status='unknown';$summary='当前日志未提供足够的问题判断依据';$next='不能据此认定运行正常。请核对日志时间和软件版本，补充失败前后的操作与响应。';}
    return ['status'=>$status,'summary'=>$summary,'next'=>$next,'issues'=>array_values($issues),'failure_lines'=>$failed,'total_lines'=>$total,'analyzed_lines'=>min($total,8000),'limited'=>$total>8000];
}
