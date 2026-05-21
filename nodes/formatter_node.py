import pandas as pd
from typing import Dict, Any
import os

def formatter_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formatter Node: รับ ID, abstractive, refs → สร้าง/อัปเดต submission.csv
    """
    print("--- RUNNING FORMATTER ---")
    query_id = state.get("query_id")
    abstractive = state.get("abstractive")
    refs = state.get("refs", [])
    
    refs_str = ",".join(refs)
    
    new_row = {
        "ID": query_id,
        "abstractive": abstractive,
        "refs": refs_str
    }
    
    csv_path = "submission.csv"
    
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        # Append or Update
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    else:
        df = pd.DataFrame([new_row])
        
    df.to_csv(csv_path, index=False)
    print(f"Saved to {csv_path}")
    
    return state