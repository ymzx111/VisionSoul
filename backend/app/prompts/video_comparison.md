本次细看并比对，必须另返回comparison：
{"target":"目标","category":"plant/place/object/scene四选一","verdict":"likely或insufficient","conclusion":"暂定判断","features":[{"frame_index":0,"detail":"可见特征"}],"candidates":[{"name":"候选","supporting_features":[0],"differences":"相似候选的差别及未确定点"}],"missing_features":["缺失特征"],"next_shot":"补拍建议","basis":"visual_inference"}
features最多5条各40字，只写可见形态、纹理、文字；推断放candidates（最多3个），supporting_features引用features的从0开始编号。conclusion80字，differences每项40字，missing_features最多3条各20字，next_shot40字。
本次没有联网检索或专业图库，候选仅为模型假设，不说已核实、不编准确率。无法区分则insufficient，candidates可为空；reply保留不确定性。未拍到不等于不存在。
植物联合叶形、叶缘、叶序、树皮、花果比对；看不清就要求近景，不虚构叶脉或硬判园艺品种，可只判断属/大类。地点需独特地标，不能因“杭州+水面”认定西湖。一次只比对同一目标的多个视角，避免混合不同主体。
