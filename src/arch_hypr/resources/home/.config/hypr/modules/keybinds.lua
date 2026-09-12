local main = "SUPER"
hl.bind(main .. " + RETURN", hl.dsp.exec_cmd("kitty"))
hl.bind(main .. " + C", hl.dsp.window.close())
hl.bind(main .. " + G", hl.dsp.exec_cmd("arch-hypr-assistant"))
hl.bind(main .. " + left", hl.dsp.focus({ direction = "left" }))
hl.bind(main .. " + right", hl.dsp.focus({ direction = "right" }))
hl.bind(main .. " + up", hl.dsp.focus({ direction = "up" }))
hl.bind(main .. " + down", hl.dsp.focus({ direction = "down" }))
for i = 1, 10 do
  local key = i % 10
  hl.bind(main .. " + " .. key, hl.dsp.focus({ workspace = i }))
  hl.bind(main .. " + SHIFT + " .. key, hl.dsp.window.move({ workspace = i }))
end
