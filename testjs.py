import json

# โหลดไฟล์ RAG ของคุณ
with open('train_rl_rag.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

total_queries = len(data['queries'])
missed_count = 0     # ดึงมาไม่ครบ/ไม่ติดเลย
perfect_count = 0    # ดึงเฉลยมาติดครบทุก Para

for q in data['queries']:
    retrieved = set(q.get('retrieved_refs', []))
    sols = set(q.get('sol_refs', []))
    
    # ถ้าไม่มีเฉลยเลยข้ามไป (กันเหนียว)
    if not sols:
        continue
        
    # เช็คว่า sol_refs ทุกตัว อยู่ใน retrieved_refs หรือไม่
    if sols.issubset(retrieved):
        perfect_count += 1
    else:
        missed_count += 1

print(f"จำนวนคำถามทั้งหมด: {total_queries} ข้อ")
print(f"✅ RAG ดึงเฉลยมาครบ: {perfect_count} ข้อ ({(perfect_count/total_queries)*100:.2f}%)")
print(f"❌ RAG ดึงเฉลยมาตกหล่น/ไม่ติดเลย: {missed_count} ข้อ ({(missed_count/total_queries)*100:.2f}%)")