# 明确指令v5：逐条配对结果

运行`20261025-1645-p1-explicit-instructions-v5`，24道新题、每题O/C各一次。O旧指令，C明确指令；两臂语法相同。

|题目|任务/类别|顺序|O完整正确|C完整正确|O/C目标字段正确|O/C格式合法|
|---|---|---|---|---|---|---|
|v5-25001-i000|extract / interactive|OC|1|1|1/1 ; 1/1|1/1|
|v5-25001-i001|extract / interactive|CO|1|1|1/1 ; 1/1|1/1|
|v5-25001-i002|extract / interactive|OC|1|1|1/1 ; 1/1|1/1|
|v5-25001-i003|extract / interactive|CO|1|0|1/1 ; 0/1|1/1|
|v5-25001-i004|check / interactive|OC|0|0|0/1 ; 0/1|1/1|
|v5-25001-i005|check / interactive|CO|1|1|1/1 ; 1/1|1/1|
|v5-25001-i006|check / interactive|OC|1|0|1/1 ; 0/1|1/1|
|v5-25001-i007|check / interactive|CO|1|1|1/1 ; 1/1|1/1|
|v5-25001-i008|organize / interactive|OC|1|1|2/2 ; 2/2|1/1|
|v5-25001-i009|organize / interactive|CO|1|1|2/2 ; 2/2|1/1|
|v5-25001-i010|organize / interactive|OC|1|1|2/2 ; 2/2|1/1|
|v5-25001-i011|organize / interactive|CO|1|1|2/2 ; 2/2|1/1|
|v5-25001-b000|extract / background|OC|1|1|6/6 ; 6/6|1/1|
|v5-25001-b001|extract / background|CO|0|1|5/6 ; 6/6|1/1|
|v5-25001-b002|extract / background|OC|0|0|5/6 ; 5/6|1/1|
|v5-25001-b003|extract / background|CO|0|0|3/6 ; 4/6|1/1|
|v5-25001-b004|check / background|OC|0|0|3/6 ; 3/6|1/1|
|v5-25001-b005|check / background|CO|0|0|3/6 ; 5/6|1/1|
|v5-25001-b006|check / background|OC|0|0|4/6 ; 3/6|1/1|
|v5-25001-b007|check / background|CO|0|1|3/6 ; 6/6|1/1|
|v5-25001-b008|organize / background|OC|0|0|5/6 ; 5/6|1/1|
|v5-25001-b009|organize / background|CO|1|0|6/6 ; 5/6|1/1|
|v5-25001-b010|organize / background|OC|1|1|6/6 ; 6/6|1/1|
|v5-25001-b011|organize / background|CO|1|1|6/6 ; 6/6|1/1|

O完整正确15/24、C完整正确14/24；目标字段分别70/88、73/88。

两臂的ID、行数、顺序和字段域由解码器限制；格式通过不代表事实正确。全部题目和原文见fulltexts，逐字段差异见summary。C24/24且技术检查通过才允许另登记校准/小负载，不自动通过G1。
