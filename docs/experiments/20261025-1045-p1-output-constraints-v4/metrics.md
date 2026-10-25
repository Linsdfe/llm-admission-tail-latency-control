# 输出约束v4：逐条配对结果

运行`20261025-1045-p1-output-constraints-v4`，24道新题、每题U/G各一次。U无语法约束，G有语法约束。

|题目|任务/类别|顺序|U完整正确|G完整正确|U/G目标字段正确|U/G格式合法|
|---|---|---|---|---|---|---|
|v4-24001-i000|extract / interactive|UG|1|1|1/1 ; 1/1|1/1|
|v4-24001-i001|extract / interactive|GU|1|1|1/1 ; 1/1|1/1|
|v4-24001-i002|extract / interactive|UG|1|1|1/1 ; 1/1|1/1|
|v4-24001-i003|extract / interactive|GU|1|1|1/1 ; 1/1|1/1|
|v4-24001-i004|check / interactive|UG|0|0|0/1 ; 0/1|0/1|
|v4-24001-i005|check / interactive|GU|0|1|0/1 ; 1/1|0/1|
|v4-24001-i006|check / interactive|UG|0|0|0/1 ; 0/1|0/1|
|v4-24001-i007|check / interactive|GU|0|1|0/1 ; 1/1|0/1|
|v4-24001-i008|organize / interactive|UG|0|1|2/2 ; 2/2|0/1|
|v4-24001-i009|organize / interactive|GU|0|1|2/2 ; 2/2|0/1|
|v4-24001-i010|organize / interactive|UG|1|1|2/2 ; 2/2|1/1|
|v4-24001-i011|organize / interactive|GU|0|1|2/2 ; 2/2|0/1|
|v4-24001-b000|extract / background|UG|1|1|6/6 ; 6/6|1/1|
|v4-24001-b001|extract / background|GU|1|1|6/6 ; 6/6|1/1|
|v4-24001-b002|extract / background|UG|0|0|5/6 ; 5/6|1/1|
|v4-24001-b003|extract / background|GU|0|0|5/6 ; 5/6|1/1|
|v4-24001-b004|check / background|UG|0|0|0/6 ; 3/6|0/1|
|v4-24001-b005|check / background|GU|0|0|0/6 ; 3/6|0/1|
|v4-24001-b006|check / background|UG|0|0|0/6 ; 3/6|0/1|
|v4-24001-b007|check / background|GU|0|0|0/6 ; 3/6|0/1|
|v4-24001-b008|organize / background|UG|1|1|6/6 ; 6/6|1/1|
|v4-24001-b009|organize / background|GU|0|0|4/6 ; 4/6|1/1|
|v4-24001-b010|organize / background|UG|1|1|6/6 ; 6/6|1/1|
|v4-24001-b011|organize / background|GU|1|1|6/6 ; 6/6|1/1|

U完整正确10/24、G完整正确15/24；目标字段分别56/88、70/88。

G的ID、行数、顺序和字段域由解码器限制；格式通过不代表事实正确。全部题目和原文见fulltexts，逐字段差异见summary。G24/24且技术检查通过才允许另登记校准/小负载，不自动通过G1。
