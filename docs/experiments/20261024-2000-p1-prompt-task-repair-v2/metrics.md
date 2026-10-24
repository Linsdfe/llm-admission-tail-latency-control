# 提示任务修订v2：客观记录

批次：`20261024-2000-p1-prompt-task-repair-v2`。40次测量、2预热另计。语义质量需要另读全文，本表不自动判定任务完成。

|请求|用途/版本|类别|输入/实际输出token|停止原因|8字符重复超额比率|
|---|---|---|---|---|---:|
|repair-01-v1|paired_sentinel / v1|interactive|151 / 64|limit|0.15294117647058825|
|repair-01-v2|paired_sentinel / v2|interactive|172 / 15|eos|0.0|
|repair-02-v2|paired_sentinel / v2|interactive|174 / 17|eos|0.0|
|repair-02-v1|paired_sentinel / v1|interactive|153 / 64|limit|0.07058823529411765|
|repair-03-v1|paired_sentinel / v1|interactive|155 / 64|limit|0.0|
|repair-03-v2|paired_sentinel / v2|interactive|176 / 25|eos|0.0|
|repair-04-v2|paired_sentinel / v2|interactive|175 / 10|eos|0.0|
|repair-04-v1|paired_sentinel / v1|interactive|154 / 49|eos|0.0|
|repair-05-v1|paired_sentinel / v1|background|327 / 384|limit|0.428|
|repair-05-v2|paired_sentinel / v2|background|375 / 351|eos|0.32854578096947934|
|repair-06-v2|paired_sentinel / v2|background|372 / 301|eos|0.37735849056603776|
|repair-06-v1|paired_sentinel / v1|background|324 / 384|limit|0.0021231422505307855|
|repair-07-v1|paired_sentinel / v1|background|331 / 384|limit|0.8639175257731959|
|repair-07-v2|paired_sentinel / v2|background|379 / 384|limit|0.5025906735751295|
|repair-08-v2|paired_sentinel / v2|background|383 / 286|eos|0.3722627737226277|
|repair-08-v1|paired_sentinel / v1|background|335 / 384|limit|0.8403141361256544|
|dev-22001-i000|fresh_development_pilot / v2|interactive|64 / 11|eos|0.0|
|dev-22001-b000|fresh_development_pilot / v2|background|134 / 384|limit|0.6948853615520282|
|dev-22001-i001|fresh_development_pilot / v2|interactive|92 / 13|eos|0.0|
|dev-22001-b001|fresh_development_pilot / v2|background|337 / 384|limit|0.5418326693227091|
|dev-22001-i002|fresh_development_pilot / v2|interactive|147 / 21|eos|0.0|
|dev-22001-b002|fresh_development_pilot / v2|background|647 / 257|eos|0.125|
|dev-22001-i003|fresh_development_pilot / v2|interactive|64 / 15|eos|0.0|
|dev-22001-b003|fresh_development_pilot / v2|background|327 / 384|limit|0.07717041800643087|
|dev-22001-i004|fresh_development_pilot / v2|interactive|91 / 10|eos|0.0|
|dev-22001-b004|fresh_development_pilot / v2|background|651 / 384|limit|0.31864406779661014|
|dev-22001-i005|fresh_development_pilot / v2|interactive|148 / 13|eos|0.0|
|dev-22001-b005|fresh_development_pilot / v2|background|138 / 384|limit|0.3407534246575342|
|dev-22001-i006|fresh_development_pilot / v2|interactive|64 / 15|eos|0.0|
|dev-22001-b006|fresh_development_pilot / v2|background|642 / 384|limit|0.28647686832740216|
|dev-22001-i007|fresh_development_pilot / v2|interactive|91 / 15|eos|0.0|
|dev-22001-b007|fresh_development_pilot / v2|background|133 / 169|eos|0.28125|
|dev-22001-i008|fresh_development_pilot / v2|interactive|147 / 9|eos|0.0|
|dev-22001-b008|fresh_development_pilot / v2|background|327 / 384|limit|0.5378787878787878|
|dev-22001-i009|fresh_development_pilot / v2|interactive|65 / 11|eos|0.0|
|dev-22001-b009|fresh_development_pilot / v2|background|138 / 384|limit|0.4787052810902896|
|dev-22001-i010|fresh_development_pilot / v2|interactive|91 / 30|eos|0.038461538461538464|
|dev-22001-b010|fresh_development_pilot / v2|background|333 / 384|limit|0.8723849372384938|
|dev-22001-i011|fresh_development_pilot / v2|interactive|147 / 19|eos|0.0|
|dev-22001-b011|fresh_development_pilot / v2|background|653 / 347|eos|0.24548736462093862|

重复指标为去空白后Unicode字符滑窗，不等同token长度或语义质量；没有自动合格阈值。EOS和达到输出上限分别记录，即使实际token恰等上限也按原生stop_type解释。

本批全部cache_prompt=false，不与旧cache开启的性能记录直接比较；提示改写也改变输入长度。8条配对是已知问题选例，24条来自固定开发家族，不作总体合格率或显著性判断。语义核读及采前32份v2进入条件另列。
