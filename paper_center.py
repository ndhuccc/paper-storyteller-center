#!/usr/bin/env python3
"""
論文說書人中心 - 核心模組（Chunk Embedding 版）
功能：論文分段索引建立、語意搜尋、Q&A 對話
"""

import json
from typing import Dict, List, Tuple
import urllib.request
from retrieval_service import rebuild_index as service_rebuild_index
from retrieval_service import search_papers as service_search_papers

# ==================== 配置 ====================
OLLAMA_BASE_URL = "http://localhost:11434"
def rebuild_index():
    """重建 Chunk Embedding 索引。"""
    return service_rebuild_index()


def search_papers(query: str, top_k: int = 5, similarity_threshold: float = 0.0) -> List[Dict]:
    """語意搜尋：搜尋最相關 chunks，依論文去重後返回。"""
    return service_search_papers(query, top_k=top_k, similarity_threshold=similarity_threshold)


# ==================== Q&A ====================
def answer_question(question: str, context_limit: int = 3) -> Tuple[str, List[Dict]]:
    """使用 RAG 回答問題"""
    results = search_papers(question, top_k=context_limit)
    if not results:
        return "抱歉，沒有找到相關的論文內容。", []
    
    context_parts = [f"=== {r.get('title','未知')} ===\n{r.get('content','')[:2000]}" for r in results]
    context = "\n\n".join(context_parts)
    
    prompt = f"""你是一個專業的論文說書人，擅長比較和解釋學術論文的內容。

根據以下論文內容，回答用戶的問題。請用繁體中文回答，並引用相關的論文標題：

=== 論文內容 ===
{context}

=== 用戶問題 ===
{question}

回答："""
    
    try:
        url = f"{OLLAMA_BASE_URL}/api/generate"
        data = {"model": "qwen3:8b", "prompt": prompt, "stream": False}
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=180) as response:
            answer = json.loads(response.read()).get('response', '').strip()
            return answer, results
    except Exception as e:
        return f"生成回答時發生錯誤：{e}", results


# ==================== 主程式 ====================
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("""
📚 論文說書人中心 - 使用說明
  python3 paper_center.py init     - 初始化並建立 chunk 索引
  python3 paper_center.py rebuild  - 重建全部索引
  python3 paper_center.py search   - 搜尋論文
  python3 paper_center.py ask      - 問問題
        """)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command in ("init", "rebuild"):
        print("🔄 重建 Chunk 索引...")
        rebuild_index()
    
    elif command == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("🔍 輸入搜尋關鍵字: ")
        results = search_papers(query)
        print(f"\n找到 {len(results)} 篇相關論文：\n")
        for i, r in enumerate(results, 1):
            sim = 1.0 - r.get('_distance', 9999)
            print(f"{i}. {r.get('title','未知')} | 相似度={sim:.2f}")
    
    elif command == "ask":
        question = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("❓ 輸入問題: ")
        print("\n🤔 思考中...")
        answer, sources = answer_question(question)
        print(f"\n📝 回答：\n{answer}\n")
        if sources:
            print("📚 參考論文：")
            for s in sources:
                print(f"  - {s.get('title','未知')}")
    
    else:
        print(f"⚠️ 未知指令: {command}")
