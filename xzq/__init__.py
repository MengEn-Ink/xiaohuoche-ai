"""辛庄桥小火车 AI 推文流水线。

链路：collector(采集) -> writer(写作) -> reviewer(审核) -> renderer(长图) -> publisher(草稿箱)。
每一步围绕一个 Article（稿件）状态机推进，状态落盘可回溯、可重跑。
"""

__version__ = "0.1.0"
