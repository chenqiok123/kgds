# 知识图谱诊断系统（KGDS）

> 独立项目。不污染第二大脑主 workspace。
> 目录：`D:\kgds\`

## 项目结构

```
kgds/
├── README.md                          # 本文件
├── data/
│   ├── roles/                         # 岗位知识图谱
│   │   └── insurance-agent/           # 保险-寿险-个险代理人
│   │       ├── nodes.json             # 55 知识节点
│   │       ├── edges.json             # 节点关系
│   │       └── tests.json             # 反盲选试题（待生成）
│   └── users/                         # 用户答题记录与图谱快照
├── web/
│   ├── index.html                     # 入口页：岗位选择+信息输入
│   ├── test.html                      # 答题页
│   ├── result.html                    # 结果页：3D图谱对比
│   └── lib/                           # 3d-force-graph 等
├── src/
│   ├── test_engine.py                 # 试题引擎：生成+反盲选检测
│   ├── graph_builder.py               # 图谱构建：应有vs实际
│   ├── flywheel.py                    # 三飞轮：自校准引擎
│   └── scorer.py                      # 评分+评估报告
└── memory/                            # 项目自身记忆
```

## 产品逻辑

```
用户输入（岗位/年龄/性别/姓名）
  → 匹配「应有知识图谱」（该岗位必须掌握的知识节点）
  → 生成反盲选试题（每节点2-3题交叉验证）
  → 用户答题
  → 生成「实际知识图谱」（点亮vs未点亮）
  → 3D对比可视化 + 完整度评估 + 学习建议
```

## 当前进度

| 阶段 | 状态 | 内容 |
|------|------|------|
| 首个岗位框架 | ✅ | 保险-寿险-个险代理人，三层55节点 |
| 知识节点写入 | ✅ | nodes.json + edges.json |
| 试题引擎+反盲选 | ✅ | test_engine.py |
| 图谱构建器 | ✅ | graph_builder.py |
| 三飞轮自校准 | ✅ | flywheel.py（图谱/反盲选/试题迭代） |
| 前端界面 | ✅ | index.html + test.html + result.html |
| 反盲选试题 | ⏳ | 待 LLM 生成真实题目 |
| 真实用户测试 | ⏳ | 需真实代理人答题数据 |
