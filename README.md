# 硅像素读出板噪声故障定位

多路奇偶校验告警下，求解**使全部异或约束同时成立的最小汉明重量故障向量**，
给出全板最优结论（而非某条局部满足的解释）。提交后取得复核编号，刷新页面
即可按编号回查故障通道与逐校验复算。

## 快速开始

```bash
# 启动页面与接口（默认宿主机端口 8080）
docker compose up --build app
# 打开 http://localhost:8080

# 运行 verify 服务：代码测试 + 构建检查 + API 冒烟，完成后退出并返回退出码
docker compose up --build --exit-code-from verify
echo $?   # 0 = 全部通过
```

## 端口与健康检查

| 变量       | 作用               | 默认值 |
|------------|--------------------|--------|
| `APP_PORT` | 宿主机映射端口     | `8080` |
| `PORT`     | 容器内监听端口     | `8000` |

```bash
APP_PORT=9000 docker compose up --build app        # 改宿主机端口
APP_PORT=9000 PORT=9000 docker compose up --build  # 同时改容器内端口
```

- 健康检查：`GET /healthz` → `{"status": "ok"}`；Dockerfile 内置 `HEALTHCHECK`，
  Compose 中 `verify` 依赖 `app` 健康后才开始。

## 页面使用

1. 录入 **2–36 个唯一通道标识**（逗号/空格/换行分隔）；
2. 录入 **1–28 条校验**：每条引用一个**非空且互不重复**的通道集合，并给出观测奇偶值（0/1）；
3. 提交后获得复核编号（地址栏变为 `?id=R-…`），刷新或按编号查询即可回看
   故障通道、选择向量与逐校验复算；
4. 输入非法、通道重复或集合重复时：页面**保留编辑内容**并**清除旧证据**
   （旧结论与地址栏编号一并移除），接口返回可定位的拒绝信息；
5. 约束不可满足时：保存**不可行结论**（同样获得复核编号），不返回近似集合。

## API

### `POST /api/reviews`

```json
{
  "channels": ["1", "2", "3"],
  "checks": [
    {"channels": ["1", "2"], "parity": 1},
    {"channels": ["2", "3"], "parity": 1},
    {"channels": ["1", "3"], "parity": 0}
  ]
}
```

- `201`：`{"review_id": "R-…", "input": …, "result": …}`，
  `result.status` 为 `optimal`（含 `fault_channels`、`weight`、
  `selection_vector`、逐校验 `recomputed/ok`）或 `infeasible`；
- `422`：`{"detail": [{"loc": ["checks", 1, "channels"], "msg": "…"}, …]}`，
  `loc` 可定位到具体字段。

### `GET /api/reviews/{review_id}`

按复核编号回查已保存结论（含不可行结论）；未知编号返回 `404`。

## 求解方法（折半综合征索引）

1. 通道按标识**自然升序**（数字段按数值）排序后**折半**为左右两段；
2. 左半枚举全部子集计算综合征，按综合征建立索引，每个综合征只保留
   （重量， 字典序键） 最优候选；
3. 右半枚举全部子集，以 `s XOR 右综合征` 精确查表合并，全局按
   （总重量， 左字典序键， 右字典序键） 取最优。

**不**枚举完整故障向量、**不**使用随机搜索、**不**以高斯消元的任意解替代
最优结论；两侧无法合并出任何候选时精确判定不可行。

**同重裁决**：在最小汉明重量的候选中，按"通道标识升序形成的选择向量"做
字典序比较，取字典序最小者（即在排序最靠前的分歧通道上取 0）。
例：候选 `{1,3} {1,4} {2,3} {2,4}` 中选中 `{2,4}`（向量 `0101`）。

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest -q                 # 代码测试
DATA_DIR=./data PORT=8000 \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
API_BASE=http://127.0.0.1:8000 python -m verify.run   # 端到端验证
```

## 目录结构

```
app/
  main.py            # FastAPI 路由与可定位校验
  solver.py          # 折半综合征索引求解器
  store.py           # SQLite 复核记录持久化
  static/index.html  # 故障定位页面
tests/               # 求解器与接口代码测试
verify/run.py        # verify 服务：构建检查 + 代码测试 + API 冒烟
Dockerfile           # 单镜像交付（含健康检查）
compose.yaml         # app + verify 编排，端口可改
```
