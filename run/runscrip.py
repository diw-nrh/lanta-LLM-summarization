import json
import random
from collections import defaultdict

def generate_few_shot_examples(json_path, output_path, samples_per_doc=30):
    # โหลดไฟล์ JSON
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    docs = data.get("docs", [])
    output_lines = []
    
    for doc in docs:
        doc_id = doc.get("doc_id", "Unknown")
        paragraphs = { p["para_id"]: p["text"] for p in doc.get("paragraphs", []) }
        qas = doc.get("qas", []) # สมมติว่า qas อยู่ในแต่ละ doc
        
        # สุ่มหยิบคำถามตามจำนวน samples_per_doc (ถ้าคำถามน้อยกว่าก็เอามาทั้งหมด)
        sampled_qas = random.sample(qas, min(len(qas), samples_per_doc))
        
        for qa in sampled_qas:
            query = qa.get("query", "")
            abstractive = qa.get("abstractive", "")
            refs = qa.get("refs", [])
            
            # ดึง Text ของแต่ละ para_id ที่เป็นคำตอบ
            ref_dict_str_list = []
            for p_id in refs:
                # เอามาเฉพาะข้อความ ถ้ายาวไปอาจจะตัด (Truncate) ก็ได้ แต่แนะนำให้เอามาเต็มๆ
                text = paragraphs.get(p_id, "").strip()
                ref_dict_str_list.append(f"{p_id}: '{text}'")
            
            ref_combined = "{" + ", ".join(ref_dict_str_list) + "}"
            
            # จัดฟอร์แมตตามที่คุณออกแบบไว้
            example_str = (
                f"[Query]: {query}\n"
                f"[refs]: {ref_combined}\n"
                f"[abstractive]: {abstractive}\n"
                f"--------------------------------------------------\n"
            )
            output_lines.append(example_str)
            
    # บันทึกลงไฟล์ .txt
    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)
        
    print(f"✅ สร้าง Examples สำเร็จ! บันทึกไว้ที่ {output_path} (รวม {len(output_lines)} ตัวอย่าง)")

# ----------------- วิธีใช้งาน -----------------
# เปลี่ยน path ให้ตรงกับไฟล์ train ของคุณ
input_json = r"D:\My-project\AI-hackathon\lanta-LLM-summarization\data\train_set.json" 
output_txt = r"D:\My-project\AI-hackathon\lanta-LLM-summarization\few_shot_examples.txt"

generate_few_shot_examples(input_json, output_txt, samples_per_doc=30)