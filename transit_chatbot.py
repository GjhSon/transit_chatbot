# transit_chatbot.py
import os
import json
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from google import genai
from dotenv import load_dotenv
import uvicorn 
from datetime import datetime

# 환경 변수 로드 
# GEMINI_API_KEY, TMAP_KEY, SQLALCHEMY_DATABASE_URL(DB연결) : .env 파일에 저장 필요
load_dotenv()

app = FastAPI()

# 클라이언트 설정 (gemini)
client = genai.Client()

# DB 연결 
DB_URL = os.getenv("SQLALCHEMY_DATABASE_URL")
engine = create_engine(DB_URL) 

class UserRequest(BaseModel):
    user_input: str


# 자바 서버로부터 받은 사용자 입력에서 출발지, 목적지, 주의사항을 추출하여 JSON형식으로 리턴
def extract_userinput_info(user_input: str):
    
    prompt = f"""
    당신은 교통 정보 추출 전문가입니다. 사용자의 문장에서 다음 정보를 추출하여 JSON 형식으로만 응답하세요.
    
    [추출 대상]
    1. departure: 출발지 (명확한 장소명, 모르면 "unknown")
    2. destination: 목적지 (명확한 장소명, 모르면 "unknown")
    3. constraints: 주의사항 (짐, 인원, 환승, 선호 수단, 장애 여부, 교통 약자 등 특이사항을 요약하여 기록, 없으면 "none")

    [제약 사항]
    - 반드시 유효한 JSON 포맷으로만 출력할 것.
    - 마크다운(```json) 기호나 부연 설명을 절대 포함하지 말고 오직 순수 JSON만을 줄 것.
    - 모든 추출된 값은 반드시 '한국어'로 작성할 것.
    - 교통과 관련 없는 문장일 경우 모든 필드를 "unknown" 또는 "none"으로 채울 것.
    
    [추출 예시]
    입력: "짐이 많은데 수원역에서 서울 롯데월드까지 환승 적은 경로 알려줘"
    출력: {{"departure": "수원역", "destination": "서울 롯데월드", "constraints": "짐이 많음, 환승 최소화"}}

    입력: "친구 3명이랑 명동에서 남산타워 갈 건데 캐리어가 있어"
    출력: {{"departure": "명동", "destination": "남산타워", "constraints": "인원 3명, 캐리어 동반"}}

    사용자의 문장: "{user_input}"
    """

    try:
        # gemini api 호출 
        response = client.models.generate_content(
            model = "gemini-2.5-flash",
            contents = prompt
        )

    
        raw_text = response.text.strip()
        
    
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        
        return json.loads(clean_json)

    except Exception as e:
        return {"error": str(e), "status": "fail"}


# gemini로부터 3개의 경로에 대한 각각의 우선순위와 추천사유를 얻어 리턴
def get_ai_recommendation(constraints: str, _3routes_summary: list):  
    routes_context = ""     
    
    for i, r in enumerate(_3routes_summary):
        routes_context += f"""
        [경로 ID: {i+1}]
        - 총 소요시간: {r['travel_time']}분, 총 환승횟수: {r['transfers']}회, 도보 거리: {r['walking_distance']}m, 총 요금: {r['fare']}원
        - 상세경로: {r['detail']}
        """

    prompt = f"""
    당신은 교통 경로 추천 전문가입니다. 사용자의 특이사항과 제공된 3가지 경로에 대한 정보(총 소요시간, 총 환승횟수, 도보 거리, 총 요금, 상세경로)를 분석하여 3가지 경로에 대해서 우선순위를 매기고 각각의 추천사유를 작성하세요.
    
    
    [사용자 특이사항]
    {constraints}

    [제공된 3가지 경로]
    {routes_context}

    [추출 대상]
    1. path_id : 제공된 경로 ID (1, 2, 3 중 해당되는 숫자)
    2. rank : 우선순위 (판단 근거가 명확하면 1, 2, 3 숫자로 응답. 판단이 어려우면 "unknown")
    3. reason : 추천사유 (사용자 특이사항과 각 경로에 대한 정보들을 반영한 구체적인 이유. 추천사유가 없으면 "unknown")

    [제약 사항]
    - 반드시 유효한 JSON 포맷으로만 출력할 것.
    - 마크다운(```json) 기호나 부연 설명을 절대 포함하지 말고 오직 순수 JSON만을 줄 것.
    - 모든 추출된 값은 반드시 '한국어'로 작성할 것.
    - 추천사유에서 "경로ID"라고 표현하지 말것. ex) 경로(ID 2) : 이런 식으로 표현하지 말 것 / 경로 2 : 이런 식으로 표현할 것.

    [출력 포맷]
    [
        {{"path_id": 1, "rank": 1, "reason": "추천 사유 1"}},
        {{"path_id": 2, "rank": 2, "reason": "추천 사유 2"}},
        {{"path_id": 3, "rank": 3, "reason": "추천 사유 3"}}
    ]
    """
    try:
        # gemini 호출
        response = client.models.generate_content(
            model = "gemini-2.5-flash",
            contents = prompt
        )        
        
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)   
    
    except Exception as e:
        return {"error": str(e), "status": "fail"}

# tmap 대중교통 api에 사용자 입력에 대한 3개의 경로 요청
def request_tmap_route(start_x, start_y, end_x, end_y):
    url = "https://apis.openapi.sk.com/transit/routes/"

    tmap_key = os.getenv("TMAP_KEY")

    current_time = datetime.now().strftime("%Y%m%d%H%M")

    payload = {
        "startX": str(start_x),
        "startY": str(start_y),
        "endX": str(end_x),
        "endY": str(end_y),
        "lang": 0,
        "format": "json",
        "count": 3,
        "searchDttm": current_time  
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "appKey": tmap_key
    }

    try: 
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:  
            return response.json()
        else:
            print(f"에러 발생: {response.status_code}")
            return None
        
    except Exception as e:
        return {"error": str(e), "status": "fail"}

        
        

    
# 각 3개의 경로에 대한 요약정보 추출: 총 소요시간, 총 환승횟수, 도보 거리, 총 요금, 세부 경로
def extract_route_summary(request_result, request_id):

    if not request_result or "metaData" not in request_result: return None

    try:
        
        itineraries = request_result["metaData"]["plan"]["itineraries"]
  

        _3_routes = []


        for path in itineraries:
            legs = path.get("legs", [])
            detail_list = []    
        
            for i, leg in enumerate(legs):
                mode = leg.get("mode")  # WALK, BUS, SUBWAY 등
                section_time = max(1, leg.get("sectionTime", 0) // 60) 
                start_name = leg.get("start", {}).get("name")
                end_name = leg.get("end", {}).get("name")
            
                if mode == "WALK":
                    dist = leg.get("distance", 0)
                    detail_list.append(f"{i+1}. 도보: '{start_name}'에서 '{end_name}'까지 약 {section_time}분 이동 ({dist}m)")

                elif mode in ["BUS", "SUBWAY"]:
                    route_name = leg.get("route", "대중교통")
                    stations = leg.get("passStopList", {}).get("stations", [])
                    station_count = len(stations) - 1 if stations else 0
                
                    detail_list.append(
                        f"{i+1}. {mode}: '{route_name}' 탑승 ('{start_name}' 승차) -> "
                        f"'{end_name}'까지 {station_count}개 정류장 이동 (약 {section_time}분)"
                )
        
            detail_fin = "\n".join(detail_list)
        


            result = {
                "request_id": request_id,
                "travel_time": path.get("totalTime", 0) // 60,
                "transfers": path.get("transferCount", 0),
                "walking_distance": path.get("totalWalkDistance", 0),
                "fare": path.get("fare", {}).get("regular", {}).get("totalFare", 0),
                "detail": detail_fin
            }

            _3_routes.append(result)
          
        return _3_routes  

    except (KeyError, IndexError) as e:
        return {"error": str(e), "status": "fail"}
        

    

@app.post("/api/v1/extract-userinput")  # 자바 서버에서 호출할 엔드포인트: 사용자 입력에서 출발지, 목적지, 주의사항을 추출하여 JSON으로 리턴
def get_info(request: UserRequest):
    result = extract_userinput_info(request.user_input)

    if not result:
        raise HTTPException(status_code=500, detail="오류 발생")

    return result


@app.post("/api/v1/routes/{request_id}")  # 자바 서버에서 호출할 엔드포인트: 해당 request_id의 출발지->목적지로 가는 3개의 경로에 대한 요약정보 추출 및 gemini를 통한 추천사유와 우선순위 생성 + DB 저장 후 성공 신호 리턴
def generate_route_recommendations(request_id: int):
    with engine.connect() as conn:
        select_query = text("SELECT departure_lon, departure_lat, destination_lon, destination_lat, user_constraints FROM user_request WHERE request_id = :rid")
        result = conn.execute(select_query, {"rid": request_id}).fetchone()

        if not result:
            raise HTTPException(status_code = 404, detail = "request_id를 찾을 수 없습니다.")

    raw_result = request_tmap_route(result.departure_lon, result.departure_lat, result.destination_lon, result.destination_lat) 
    summaries = extract_route_summary(raw_result, request_id) 

    if not summaries:
        return {"status": "fail", "message": "경로를 찾지 못했습니다."}
    
    # gemini로부터 각 3개의 경로에 대한 추천사유와 우선순위를 가져옴
    ai_recommendations = get_ai_recommendation(result.user_constraints, summaries)
    
    
    if isinstance(ai_recommendations, dict) and ai_recommendations.get("status") == "fail":
        print("Gemini API 호출 실패")
        ai_recommendations = []


    
    # 모든 결과(총 소요시간, 총 환승횟수, 도보 거리, 총 요금, 추천사유, 우선순위, 세부경로)를 DB에 저장
    with engine.begin() as conn:  
        for i, s in enumerate(summaries):
            
            recommendation = next((item for item in ai_recommendations if item.get('path_id') == i+1), None)  # 현재 처리할 경로 id(i+1)와 동일한 id를 가지는 gemini 추천 결과를 가져옴
            
            if recommendation is None:
                recommendation = {}
            
            raw_rank = recommendation.get('rank')
            if str(raw_rank).isdigit():
               rank = int(raw_rank)
            else:
                rank = i + 1  

            reason = recommendation.get('reason')
            if not reason or reason == 'unknown':
                reason = "여러 기준을 종합적으로 반영한 추천 경로입니다."

            insert_query = text("""
                INSERT INTO route_summary (request_id, travel_time, transfers, walking_distance, fare, detail, `rank`, `reason`)
                VALUES (:rid, :time, :trans, :walk, :fare, :detail, :rank, :reason)
            """)
            conn.execute(insert_query, {
                "rid": s['request_id'],
                "time": s['travel_time'],
                "trans": s['transfers'],
                "walk": s['walking_distance'],
                "fare": s['fare'],
                "detail": s['detail'],
                "rank": rank,
                "reason": reason
            })
    

    # 성공 시 자바 서버에 성공 신호와 성공 경로 개수를 리턴
    return {"status": "success", "data_count": len(summaries)}  



if __name__ == "__main__":
    uvicorn.run(app, host = "0.0.0.0", port = 8000)