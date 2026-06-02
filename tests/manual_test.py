from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient('192.168.117.30', port=502)
client.connect()

result = client.read_holding_registers(address=1, count=1, slave=255)
raw = result.registers[0]

# Values are scaled integers - check if > 32767 (negative number in 16-bit signed)
if raw > 32767:
    raw -= 65536

temperature = raw / 10.0  # typically 1 decimal place scaling
print(f"Temperature: {temperature} °C")

client.close()