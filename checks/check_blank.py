import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import semantic_model as sm
sm.query("""EVALUATE ROW(
  "rejection rows", COUNTROWS(Rejections),
  "unmatched", COUNTROWS(FILTER(Rejections, ISBLANK(RELATED('Pipeline Layers'[Month Type Key])))),
  "layer rows", COUNTROWS('Pipeline Layers'),
  "distinct layer keys", DISTINCTCOUNTNOBLANK('Pipeline Layers'[Month Type Key]))""")
sm.query("""EVALUATE TOPN(5, FILTER(Rejections, ISBLANK(RELATED('Pipeline Layers'[Month Type Key]))))""")
