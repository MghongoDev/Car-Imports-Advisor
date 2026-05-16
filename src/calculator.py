def calculate_import_cost(price_jpy, engine_cc, freight_jpy=250000):
    """
    Calculates the total cost of importing a car to Kenya based on KRA rules.
    
    Args:
        price_jpy (int): FOB Price of the car in JPY
        engine_cc (int): Engine capacity in CC
        freight_jpy (int): Estimated shipping cost (default 250k JPY)
    
    Returns:
        dict: Breakdown of costs
    """
    
    # Exchange Rate (Approximate: 1 JPY = 0.95 KES)
    EXCHANGE_RATE = 0.95
    
    # 1. Convert to KES
    fob_kes = price_jpy * EXCHANGE_RATE
    freight_kes = freight_jpy * EXCHANGE_RATE
    insurance_kes = fob_kes * 0.015 # Insurance is usually 1.5% of FOB
    
    # CIF Value (Cost, Insurance, Freight)
    cif_kes = fob_kes + freight_kes + insurance_kes
    
    # 2. Import Duty (25% of CIF)
    import_duty = 0.25 * cif_kes
    
    # 3. Excise Duty (Based on Engine Size)
    # Base for Excise = CIF + Import Duty
    excise_base = cif_kes + import_duty
    
    if engine_cc <= 1500:
        excise_rate = 0.20
    elif 1500 < engine_cc <= 2500:
        excise_rate = 0.30
    else:
        excise_rate = 0.35
        
    excise_duty = excise_base * excise_rate
    
    # 4. VAT (16% of CIF + Import Duty + Excise Duty)
    vat_base = cif_kes + import_duty + excise_duty
    vat = 0.16 * vat_base
    
    # 5. IDF (Import Declaration Fee) - 3.5% of CIF
    idf = 0.035 * cif_kes
    
    # 6. RDL (Railway Development Levy) - 2% of CIF
    rdl = 0.02 * cif_kes
    
    # 7. Estimated Port & Clearance Fees (Fixed estimates)
    port_charges = 50000 # KES
    clearing_agent = 35000 # KES
    registration = 15000 # KES (NTSA)
    
    # TOTALS
    total_taxes = import_duty + excise_duty + vat + idf + rdl
    total_landed_cost = cif_kes + total_taxes + port_charges + clearing_agent + registration
    
    return {
        "CIF (KES)": round(cif_kes),
        "Import Duty (KES)": round(import_duty),
        "Excise Duty (KES)": round(excise_duty),
        "VAT (KES)": round(vat),
        "IDF & RDL (KES)": round(idf + rdl),
        "Port & Clearing (KES)": port_charges + clearing_agent + registration,
        "Total Landed Cost (KES)": round(total_landed_cost)
    }
