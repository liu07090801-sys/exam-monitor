# 优化进度与待办清单

> 本文件记录屏幕监控系统的优化进度。已完成的工作已验证可运行,未完成项下次继续。

---

## 已完成

### 第一轮优化(已全部完成并测试)
- [x] 冻结检测逻辑重写:从"相邻帧哈希相同"改为"在线但超过 freeze_timeout 秒无截图帧"
- [x] 截图文件名加微秒(%H%M%S_%f),修复同秒覆盖;服务端提为 screenshot_filename()
- [x] TLS 加密落地:build_server_ssl_context / build_client_ssl_context,wss 握手已验证
- [x] register_rejected 改为彻底停止,不再无限重连
- [x] zombie 连接清理:心跳超时主动 ws.close
- [x] is_window_allowed 生成器陷阱修复
- [x] 截图哈希存证回填(update_last_screenshot_hash)
- [x] image_hash 用 tobytes() 消除 Pillow 弃用警告
- [x] freeze_timeout 配置化(config.yaml/config.py)
- [x] 单元测试 33 个:tests/test_*.py

### 第二轮 bug 修复(修复已完成,已回归通过)
- [x] P0: 客户端豁免自身锁定窗口(focus_check_loop 中 hwnd == root.winfo_id() 判为 self 状态)
- [x] P0: 服务端消息解析防护(json.JSONDecodeError / 非 dict / 缺 client_id 均安全跳过或关闭)
- [x] P0: 旧连接 finally 不再误标离线(仅当 ws is websocket 时才置离线+记日志)
- [x] P1: psutil 缺失降级(_proc_name_from_hwnd 用 Win32 API 查进程名,进程白名单功能不失效)
- [x] P1: check_online_loop 重构:锁内只收集事件,锁外 add_log(避免持锁磁盘 IO)
- [x] P1: 状态灯红色改为时间窗口(alert_color 纯函数,alert_hold_seconds 默认 300 秒后恢复绿色)
- [x] P1: zombie ws 关闭后引用置 None(条件判断,不误清重连新连接)
- [x] P1: stop_all_clients 并行提交+统一 3 秒总超时,避免 N 客户端串行阻塞 GUI
- [x] P1: 客户端退出时 loop.stop 的 RuntimeError 噪音消除
- [x] P1: 日志 tag 样式(tag_configure info/alert 颜色)
- [x] 新增 test_alert_color.py,当前共 39 个测试全部通过

### 第三轮功能优化(本轮完成)
- [x] **考试网址+全屏强制**(核心新需求):浏览器必须全屏且停留在白名单考试网址(洛谷),
      切换/退出网址、未全屏均判违规报警
  - classify_window 纯函数统一判定(self/desktop/allowed/violation),便于单元测试
  - UI Automation 读浏览器地址栏(可选依赖 uiautomation,缺失自动降级标题匹配,限 600 节点/0.8 秒预算)
  - exam.url_whitelist 支持域名/子域与 域名+路径前缀 匹配,lookalike 域名(evil-luogu.com.cn.evil.com)不误放行
  - exam.grace_seconds 启动宽限期,供考生打开浏览器进入考试页
  - exam.strict_url_check 严格模式开关(读不到网址时是否直接判违规)
- [x] **报警即截图**:确认违规瞬间先抓屏取证(本地存档+二进制帧上传+哈希存证),再弹警示,
      确保证据画面不含警示遮罩;持续违规仅刷新报警信息,不重复拍到遮罩
- [x] **锁定 GUI 遮挡修复**:client.lock_mode=popup(默认)窗口平时隐藏,违规时才弹出全屏警示,
      恢复合规自动收起;彻底解决"截图一直是锁定画面"问题(overlay 保留旧行为可选);
      断线时弹角落状态窗(不阻塞考试),重连自动隐藏
- [x] 报警信息增强:违规原因(浏览器未全屏/考试网址不符/切屏)+实际网址随 alert 上报,监考端日志/横幅/卡片展示
- [x] 多显示器拼接修正:compose_monitor_images 按虚拟屏幕坐标布局(负坐标/纵向排列不再错位,纯函数可测)
- [x] 落盘截图总量上限:server.max_screenshots_disk,超限删最旧(enforce_screenshot_cap)
- [x] 监考老师口令鉴权:server.teacher_password,非空时结束监考/导出日志需验证(留空=原行为)
- [x] view_screenshots 翻页窗口改为每次渲染读取最新 history(打开期间新截图可翻看),去除按钮重绑定 hack
- [x] README 更新:alert_hold_seconds 说明、客户端自我报警豁免 Q&A、exam 配置、lock_mode、依赖表
- [x] 新增 tests/test_exam_enforce.py、tests/test_screen_compose.py,测试总数 39 -> 70

### 第四轮 bug 复查(本轮完成,回归 70 测试通过)
- [x] 高危修复:get_browser_url 地址栏候选优先级——页面编辑框(如洛谷代码编辑器)里的 URL 文本
      不再被误判为地址栏;提取 _pick_url_from_edits 纯函数(地址栏命名 > 像 URL 的值 > 地址栏命名非空值),新增 5 个用例
- [x] hide_alert 取消 pending 自动隐藏定时器,旧定时器不再提前撤掉新一轮违规警示
- [x] 合规恢复仅在警示态(alert)调度撤除,减少每 tick 冗余排队
- [x] popup 角落状态窗隐藏大字号提示区(小窗只显示状态栏,避免挤压变形)
- [x] 监考端报警横幅时长改读 alert_flash_duration(原硬编码 5000ms 与配置不符)

### 当前测试状态
```
python -m unittest discover -s tests -v
# Ran 70 tests ... OK
```

---

## 下次待办(尚未完成)

### 待验证项
- [ ] 真机端到端冒烟:启动 server.py + client.py 双进程联调,确认:
  1. popup 模式违规弹出/恢复收起,证据截图不含警示遮罩
  2. 真机浏览器 F11 全屏判定与地址栏读取(chrome/edge/firefox 各验证一次)
  3. 监考端卡片颜色时间窗口行为(报警 300 秒后恢复绿色)
  4. 冻结检测在真实 force_send_interval 下不误报
- [ ] 多显示器拼接实机验证(主屏+副屏、副屏在左侧负坐标场景)
- [ ] 宽限期时长按真实考场准备时间调参

### 待改进项(设计层面,需用户确认)
- [ ] 多考场支持(room_id 字段,单服务承载多考场)
- [ ] 断线期间数据补传(客户端本地缓冲+重连后回放)
- [ ] 监考老师口令进一步做哈希存储/防爆破锁定(当前明文配置、无尝试次数限制)
- [ ] tkinter 跨线程 root.after 的理论竞态(当前为常见模式,极端并发下有风险)
- [ ] UI Automation 地址栏控件精确定位(当前整树遍历有 600 节点上限,可进一步按 AutomationId 加速)
- [ ] 截图总量上限按考生维度分摊(当前为全局总量)

---

*保存时间: 2026-08-20(第四轮 bug 复查完成,回归 70 测试通过)*
