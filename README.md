这是一个自动推送设定检索式的 telegram 机器人，旨在帮助用户获取最新前沿文章。

根据用户设定的时间，自动检索文章，并将新文章推送给用户。附带 AI 打 tag 和总结功能。推送效果如下：

```
Person-Centric Annotations of LAION-400M: Auditing Bias and Its Transfer to Models
Authors: Leander Girrbach, Stephan Alaniz, Genevieve Smith, Trevor Darrell, Zeynep Akata
Published: 2025-10-04T07:51:59Z
Tags: 视觉语言模型, 数据集偏见, 人口统计标注, 模型审计, LAION-400M
Summary: 本研究通过为LAION-400M数据集创建全面的人物中心标注（包括2.76亿个边界框、感知性别/种族标签及自动生成描述），首次建立了大规模数据集组成与下游模型偏差之间的实证联系。研究发现数据集存在显著人口统计失衡和有害关联，例如黑人及中东裔男性与犯罪负面内容的不当关联。实验表明CLIP和Stable Diffusion模型中60-70%的性别偏差可直接归因于训练数据的共现模式，为理解视觉语言模型偏差来源提供了重要依据。
Comment: 48 pages
Categories: cs.CV, cs.CL, cs.CY, cs.LG
Continue: Links (http://arxiv.org/abs/2510.03721v1) | PDF (http://arxiv.org/pdf/2510.03721v1) | Ar5iv (https://ar5iv.labs.arxiv.org/html/2510.03721v1)
```

requirement.txt

```
telegram
sqlalchemy
pyyaml
httpx
arxiv
python-telegram-bot
psycopg2
"python-telegram-bot[rate-limiter]"
"python-telegram-bot[socks]"
pysocks
```

重置数据库

```shell
psql -U postgres -d arxiv_bot
```

```sql
\d
DROP TABLE IF EXISTS paper_user_notify CASCADE;
DROP TABLE IF EXISTS papers CASCADE;
DROP TABLE IF EXISTS user_config CASCADE;

DROP TYPE IF EXISTS papers CASCADE;
DROP TYPE IF EXISTS paper_user_notify CASCADE;
DROP TYPE IF EXISTS user_config CASCADE;
```
