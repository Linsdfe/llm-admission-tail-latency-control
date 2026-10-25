# 可核对资料任务v3：逐条确定性核对

批次：`20261025-1015-p1-verifiable-tasks-v3`。24测量、2预热另计。主规则只标准化良性空白，不改ID、值、标签或顺序。

|请求|任务/类别|输入/输出token|停因|事实与顺序完整正确|原文完全相同|主要差异|
|---|---|---|---|---|---|---|
|v3-23001-i000|extract / interactive|39 / 6|eos|是|是|—|
|v3-23001-i001|extract / interactive|39 / 3|eos|否|否|结构/说明、漏ID、顺序/覆盖|
|v3-23001-i002|extract / interactive|81 / 6|eos|是|是|—|
|v3-23001-i003|extract / interactive|150 / 6|eos|是|是|—|
|v3-23001-i004|check / interactive|81 / 18|eos|否|否|值、多ID、顺序/覆盖|
|v3-23001-i005|check / interactive|81 / 14|eos|否|否|值、多ID、重复ID、顺序/覆盖|
|v3-23001-i006|check / interactive|153 / 21|eos|否|否|结构/说明、漏ID、顺序/覆盖|
|v3-23001-i007|check / interactive|57 / 11|eos|否|否|值、多ID、顺序/覆盖|
|v3-23001-i008|organize / interactive|147 / 64|limit|否|否|结构/说明、多ID、顺序/覆盖|
|v3-23001-i009|organize / interactive|147 / 64|limit|否|否|结构/说明、多ID、顺序/覆盖|
|v3-23001-i010|organize / interactive|49 / 16|eos|否|否|多ID、顺序/覆盖|
|v3-23001-i011|organize / interactive|85 / 48|eos|否|否|多ID、顺序/覆盖|
|v3-23001-b000|extract / background|132 / 36|eos|是|是|—|
|v3-23001-b001|extract / background|132 / 25|eos|否|否|值、漏ID、顺序/覆盖|
|v3-23001-b002|extract / background|321 / 36|eos|是|是|—|
|v3-23001-b003|extract / background|643 / 36|eos|是|是|—|
|v3-23001-b004|check / background|331 / 30|eos|否|否|值|
|v3-23001-b005|check / background|331 / 42|eos|否|否|值|
|v3-23001-b006|check / background|653 / 60|eos|否|否|结构/说明、漏ID、顺序/覆盖|
|v3-23001-b007|check / background|139 / 36|eos|否|否|值|
|v3-23001-b008|organize / background|645 / 36|eos|否|否|值|
|v3-23001-b009|organize / background|645 / 36|eos|是|是|—|
|v3-23001-b010|organize / background|134 / 36|eos|是|是|—|
|v3-23001-b011|organize / background|323 / 36|eos|是|是|—|

主核对正确9/24；原始字节完全相同9/24。各分项会重叠，不相加成单一分数。

24/24主核对正确才达到另登记新长度及小开发负载的进入条件；这不是G1通过或总体业务质量保证。EOS、未触顶和技术计数通过不能代替答案核对。完整原文与逐字段差异见summary/fulltexts，助手仍需对全部24原题和输出补核实际任务一致性。
