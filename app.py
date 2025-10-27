from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
from pytrends.request import TrendReq

app = FastAPI(title="GEO Trends API")

class TrendsRequest(BaseModel):
    queries: List[str]
    geo: str = "AR"
    timeframe: str = "today 12-m"
    hl: str = "es-AR"
    tz: int = 180

@app.post("/trends")
def get_trends(req: TrendsRequest):
    pytrends = TrendReq(hl=req.hl, tz=req.tz)
    pytrends.build_payload(req.queries, timeframe=req.timeframe, geo=req.geo)
    data = pytrends.interest_over_time()

    results = []
    if data is not None and not data.empty:
        for q in req.queries:
            serie = data[q].dropna().tolist()
            volume_avg = round(sum(serie)/len(serie), 2) if serie else 0
            volume_last = int(serie[-1]) if serie else 0
            results.append({
                "query": q,
                "volume_avg": volume_avg,
                "volume_last": volume_last
            })
    return {"geo": req.geo, "items": results}
