import pandas as pd
from typing import Dict, Any
import os

def formatter_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formatter Node: รับ ID, abstractive, refs → สร้าง/อัปเดต submission.csv
    """
    print("--- RUNNING FORMATTER ---")
    query_id = state.get("query_id")
    abstractive = state.get("abstractive", "")
    refs = state.get("refs", [])
    
    # กฎของ Refs: หลายอันให้คั่นด้วย comma ไม่มี space (เช่น P4,P3,P1)
    refs_str = ",".join(refs) if refs else ""
    
    new_row = {
        "ID": query_id,
        "abstractive": abstractive,
        "refs": refs_str
    }
    
    csv_path = r"result/submission.csv"
    
    try:
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            df = pd.read_csv(csv_path)
            # ถ้ามี ID นี้อยู่แล้ว ให้อัปเดตข้อมูลแทนการต่อท้าย
            if query_id in df['ID'].values:
                df.loc[df['ID'] == query_id, ['abstractive', 'refs']] = [abstractive, refs_str]
            else:
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            df = pd.DataFrame([new_row])
            
        df.to_csv(csv_path, index=False, encoding='utf-8')
        print(f"[SUCCESS] Saved {query_id} to {csv_path} | Refs: {refs_str}")
    except Exception as e:
        print(f"[ERROR] Failed to save CSV: {e}")
    
    return state