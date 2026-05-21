from graph import app

def main():
    print("--- STARTING SYSTEM ---")
    
    # จำลอง Input
    inputs = {
        "query_id": "Q0008",
        "doc_id": "doc_054",
        "query": "การประชุมครั้งนี้ได้ข้อสรุปว่าอย่างไร",
        "retry_count": 0
    }
    
    print(f"Processing Query: {inputs['query_id']}")
    
    # รัน Graph
    for output in app.stream(inputs):
        for node_name, node_state in output.items():
            print(f"--- FINISHED NODE: {node_name} ---")
            
    print("--- PIPELINE COMPLETE ---")

if __name__ == "__main__":
    main()
