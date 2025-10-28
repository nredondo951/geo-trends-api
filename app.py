from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from pytrends.request import TrendReq
from pytrends.exceptions import TooManyRequestsError
import time, random, traceback

app = FastAPI(title="GEO Trends API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TrendsRequest(BaseModel):
    queries: List[str] = Field(..., description="Lista de términos (máx 25 recomendado)")
    geo: str = "AR"
    timeframe: str = "today 12-m"
    hl: str = "es-AR"
    tz: int = 180

@app.get("/health")
def health():
    return {"ok": True}

def chunks(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def fetch_iot_with_retry(pytrends: TrendReq, batch: List[str], geo: str, timeframe: str,
                         max_retries: int = 4):
    """
    Llama a interest_over_time con reintentos exponenciales ante 429/errores transitorios.
    """
    attempt = 0
    while True:
        try:
            pytrends.build_payload(batch, timeframe=timeframe, geo=geo)
            return pytrends.interest_over_time()
        except TooManyRequestsError as e:
            if attempt >= max_retries:
                print("TooManyRequestsError: no mas reintentos", repr(e))
                raise
            delay = (2 ** attempt) * 3 + random.uniform(0.0, 1.0)  # 3s,6s,12s,24s + jitter
            print(f"429 en batch {batch} → retry en {delay:.1f}s (attempt {attempt+1}/{max_retries})")
            time.sleep(delay)
            attempt += 1
        except Exception as e:
            if attempt >= max_retries:
                print("Error transitorio sin resolver:", repr(e))
                raise
            delay = (2 ** attempt) * 2 + random.uniform(0.0, 0.8)
            print(f"Error {type(e).__name__} en batch {batch} → retry en {delay:.1f}s")
            time.sleep(delay)
            attempt += 1

@app.post("/trends")
def get_trends(req: TrendsRequest) -> Dict[str, Any]:
    queries = [q.strip() for q in req.queries if isinstance(q, str) and q.strip()]
    if not queries:
        raise HTTPException(status_code=400, detail="La lista 'queries' está vacía.")

    # Limitar para no gatillar 429 en IP compartida
    if len(queries) > 15:
        queries = queries[:15]

    BATCH_SIZE = 3
    SLEEP_BETWEEN_BATCHES = 6.0

    items: List[Dict[str, Any]] = []
    avg_map: Dict[str, float] = {}

    try:
        pytrends = TrendReq(hl=req.hl, tz=req.tz)

        for batch in chunks(queries, BATCH_SIZE):
            try:
                iot = fetch_iot_with_retry(pytrends, batch, req.geo, req.timeframe, max_retries=4)
            except TooManyRequestsError:
                for q in batch:
                    items.append({
                        "query": q,
                        "volume_avg_index": 0,
                        "volume_last_index": 0,
                        "volume_max_index": 0,
                        "sparkline": [],
                        "error": "429"
                    })
                time.sleep(SLEEP_BETWEEN_BATCHES + 4.0)
                continue
            except Exception as e:
                print("Fallo lote (no 429):", repr(e))
                print(traceback.format_exc())
                for q in batch:
                    items.append({
                        "query": q,
                        "volume_avg_index": 0,
                        "volume_last_index": 0,
                        "volume_max_index": 0,
                        "sparkline": [],
                        "error": type(e).__name__
                    })
                time.sleep(SLEEP_BETWEEN_BATCHES)
                continue

            if iot is None or iot.empty:
                for q in batch:
                    items.append({
                        "query": q,
                        "volume_avg_index": 0,
                        "volume_last_index": 0,
                        "volume_max_index": 0,
                        "sparkline": []
                    })
            else:
                for q in batch:
                    serie = iot[q].dropna().astype(int).tolist() if q in iot.columns else []
                    if serie:
                        avg = sum(serie)/len(serie)
                        last = serie[-1]; mx = max(serie)
                        avg_map[q] = avg
                        items.append({
                            "query": q,
                            "volume_avg_index": round(avg, 2),
                            "volume_last_index": int(last),
                            "volume_max_index": int(mx),
                            "sparkline": serie[-26:]
                        })
                    else:
                        items.append({
                            "query": q,
                            "volume_avg_index": 0,
                            "volume_last_index": 0,
                            "volume_max_index": 0,
                            "sparkline": []
                        })

            time.sleep(SLEEP_BETWEEN_BATCHES)

        global_max = max(avg_map.values(), default=0)
        for it in items:
            base = it.get("volume_avg_index", 0)
            it["volume_score"] = round((base / global_max) * 100, 1) if global_max > 0 else 0

        return {
            "geo": req.geo,
            "timeframe": req.timeframe,
            "note": "Índices 0–100 de Google Trends (no cantidades absolutas).",
            "items": items
        }

    except Exception as e:
        print("Unhandled error in /trends:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal error: {type(e).__name__}: {str(e)}")
