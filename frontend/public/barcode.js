/* Product codes identify packages; serialized GS1 payloads must match by GTIN. */
function productBarcode(scan) {
  let code = String(scan).trim();
  if (!code || code.length > 512) throw Error('Scan one product barcode, then press Enter if your scanner does not send it automatically.');
  const gs1 = /^\](?:d2|C1)/.test(code) || code.startsWith('(01)');
  code = code.replace(/^\](?:d2|C1|E0|C0)/, '');
  if (gs1 || (/^01\d{14}/.test(code) && code.length >= 16)) {
    const match = code.match(/^(?:\(01\)|01)(\d{14})/);
    if (!match) throw Error('This GS1 scan does not start with a product GTIN. Scan the medicine’s product barcode.');
    code = match[1];
  }
  if (/^\d+$/.test(code) && [8,12,13,14].includes(code.length)) {
    const digits = code.split('').map(Number);
    const check = digits.pop();
    const total = digits.reverse().reduce((sum, value, i) => sum + value * (i % 2 ? 1 : 3), 0);
    if ((10 - total % 10) % 10 !== check) throw Error('The barcode check digit is invalid. Please scan the package again.');
    return code.padStart(14, '0');
  }
  if (gs1 || code.length > 100 || /[^\x20-\x7e]/.test(code)) throw Error('This scan cannot be used as a product code. Check your scanner’s keyboard output settings and scan again.');
  return code;
}
