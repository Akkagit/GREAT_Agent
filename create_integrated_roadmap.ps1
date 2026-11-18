param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'output')
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
}

$PageWidth = 18.5
$PageHeight = 11.5

$Colors = @{
    Ink          = 'RGB(25,49,83)'
    Muted        = 'RGB(82,101,128)'
    Grey         = 'RGB(110,121,135)'
    LightGrey    = 'RGB(233,238,244)'
    White        = 'RGB(255,255,255)'
    Blue         = 'RGB(45,112,224)'
    BlueDark     = 'RGB(31,83,166)'
    BlueFill     = 'RGB(239,245,255)'
    Green        = 'RGB(23,147,82)'
    GreenDark    = 'RGB(18,112,65)'
    GreenFill    = 'RGB(239,249,242)'
    Orange       = 'RGB(238,119,22)'
    OrangeDark   = 'RGB(190,82,10)'
    OrangeFill   = 'RGB(255,247,238)'
    Purple       = 'RGB(111,76,184)'
    PurpleDark   = 'RGB(75,48,137)'
    PurpleFill   = 'RGB(247,243,255)'
    DeepFill     = 'RGB(231,239,252)'
}

function Set-CellFormula {
    param($Shape, [string]$CellName, [string]$Formula)
    try { $Shape.CellsU($CellName).FormulaU = $Formula } catch { }
}

function Add-Box {
    param(
        [double]$Left,
        [double]$Top,
        [double]$Width,
        [double]$Height,
        [string]$Text,
        [string]$Fill,
        [string]$Line,
        [double]$FontSize = 9.5,
        [bool]$Bold = $false,
        [double]$LineWeight = 1.2,
        [double]$Radius = 0.08
    )
    $bottom = $PageHeight - $Top - $Height
    $topY = $PageHeight - $Top
    $shape = $script:Page.DrawRectangle($Left, $bottom, $Left + $Width, $topY)
    $shape.Text = $Text
    Set-CellFormula $shape 'FillForegnd' $Fill
    Set-CellFormula $shape 'FillPattern' '1'
    Set-CellFormula $shape 'LineColor' $Line
    Set-CellFormula $shape 'LineWeight' ("{0} pt" -f $LineWeight)
    Set-CellFormula $shape 'Rounding' ("{0} in" -f $Radius)
    Set-CellFormula $shape 'Char.Color' $Colors.Ink
    if ($null -ne $script:FontId) { Set-CellFormula $shape 'Char.Font' ("{0}" -f $script:FontId) }
    Set-CellFormula $shape 'Char.Size' ("{0} pt" -f $FontSize)
    Set-CellFormula $shape 'Char.Style' $(if ($Bold) { '1' } else { '0' })
    Set-CellFormula $shape 'Para.HorzAlign' '1'
    Set-CellFormula $shape 'VerticalAlign' '1'
    Set-CellFormula $shape 'TextBlockMarginLeft' '0.08 in'
    Set-CellFormula $shape 'TextBlockMarginRight' '0.08 in'
    Set-CellFormula $shape 'TextBlockMarginTop' '0.03 in'
    Set-CellFormula $shape 'TextBlockMarginBottom' '0.03 in'
    return $shape
}

function Add-Text {
    param(
        [double]$Left,
        [double]$Top,
        [double]$Width,
        [double]$Height,
        [string]$Text,
        [double]$FontSize = 10,
        [bool]$Bold = $false,
        [string]$Color = $Colors.Ink,
        [int]$Align = 1
    )
    $shape = Add-Box -Left $Left -Top $Top -Width $Width -Height $Height -Text $Text -Fill $Colors.White -Line $Colors.White -FontSize $FontSize -Bold $Bold -LineWeight 0
    Set-CellFormula $shape 'FillPattern' '0'
    Set-CellFormula $shape 'LinePattern' '0'
    Set-CellFormula $shape 'Char.Color' $Color
    Set-CellFormula $shape 'Para.HorzAlign' "$Align"
    return $shape
}

function Add-Line {
    param(
        [double]$X1,
        [double]$Y1,
        [double]$X2,
        [double]$Y2,
        [string]$Color = $Colors.Grey,
        [double]$Weight = 1.4,
        [bool]$Arrow = $true,
        [bool]$Dashed = $false
    )
    $line = $script:Page.DrawLine($X1, $PageHeight - $Y1, $X2, $PageHeight - $Y2)
    Set-CellFormula $line 'LineColor' $Color
    Set-CellFormula $line 'LineWeight' ("{0} pt" -f $Weight)
    if ($Arrow) { Set-CellFormula $line 'EndArrow' '13' }
    if ($Dashed) { Set-CellFormula $line 'LinePattern' '2' }
    return $line
}

function Add-ElbowArrow {
    param(
        [double]$X1,
        [double]$Y1,
        [double]$XMid,
        [double]$X2,
        [double]$Y2,
        [string]$Color = $Colors.Grey,
        [double]$Weight = 1.4,
        [bool]$Dashed = $false
    )
    Add-Line -X1 $X1 -Y1 $Y1 -X2 $XMid -Y2 $Y1 -Color $Color -Weight $Weight -Arrow $false -Dashed $Dashed | Out-Null
    Add-Line -X1 $XMid -Y1 $Y1 -X2 $XMid -Y2 $Y2 -Color $Color -Weight $Weight -Arrow $false -Dashed $Dashed | Out-Null
    Add-Line -X1 $XMid -Y1 $Y2 -X2 $X2 -Y2 $Y2 -Color $Color -Weight $Weight -Arrow $true -Dashed $Dashed | Out-Null
}

function Add-SectionPanel {
    param(
        [double]$Left,
        [double]$Top,
        [double]$Width,
        [double]$Height,
        [string]$Fill,
        [string]$Line,
        [string]$Title,
        [string]$Subtitle,
        [string]$Number
    )
    $panel = Add-Box -Left $Left -Top $Top -Width $Width -Height $Height -Text '' -Fill $Fill -Line $Line -LineWeight 1.5 -Radius 0.12
    Set-CellFormula $panel 'LinePattern' '2'
    Add-Text -Left ($Left + 0.18) -Top ($Top + 0.14) -Width 0.45 -Height 0.34 -Text $Number -FontSize 13 -Bold $true -Color $Line | Out-Null
    Add-Text -Left ($Left + 0.62) -Top ($Top + 0.10) -Width ($Width - 0.82) -Height 0.42 -Text $Title -FontSize 13 -Bold $true -Color $Colors.Ink -Align 0 | Out-Null
    Add-Text -Left ($Left + 0.22) -Top ($Top + 0.49) -Width ($Width - 0.44) -Height 0.3 -Text $Subtitle -FontSize 7.8 -Bold $false -Color $Colors.Muted -Align 0 | Out-Null
    return $panel
}

function Add-StepArrow {
    param(
        [double]$X,
        [double]$Y1,
        [double]$Y2,
        [string]$Color
    )
    Add-Line -X1 $X -Y1 $Y1 -X2 $X -Y2 $Y2 -Color $Color -Weight 1.8 -Arrow $true | Out-Null
}

$visio = $null
$document = $null

try {
    $visio = New-Object -ComObject Visio.InvisibleApp
    $document = $visio.Documents.Add('')
    $script:Page = $document.Pages.Item(1)
    $script:Page.Name = '综合技术路线图'
    $script:FontId = $null
    $fontCollection = $document.Fonts
    for ($fontIndex = 1; $fontIndex -le $fontCollection.Count; $fontIndex++) {
        $candidateFont = $fontCollection.Item($fontIndex)
        if ($candidateFont.Name -eq '微软雅黑') {
            $script:FontId = $candidateFont.ID
            break
        }
    }

    Set-CellFormula $script:Page.PageSheet 'PageWidth' "$PageWidth in"
    Set-CellFormula $script:Page.PageSheet 'PageHeight' "$PageHeight in"
    Set-CellFormula $script:Page.PageSheet 'ShdwPattern' '0'

    # Page background and title
    Add-Box -Left 0 -Top 0 -Width $PageWidth -Height $PageHeight -Text '' -Fill $Colors.White -Line $Colors.White -LineWeight 0 -Radius 0 | Out-Null
    Add-Text -Left 0.5 -Top 0.18 -Width 17.5 -Height 0.5 -Text 'GREAT Agent 驱动的 BDS-3 PPP-B2b 模糊度固定技术路线' -FontSize 21 -Bold $true -Color $Colors.Ink | Out-Null
    Add-Text -Left 1.0 -Top 0.68 -Width 16.5 -Height 0.34 -Text '从多源观测与改正数、模糊度偏差产品生成，到智能任务编排、固定解验证与结果反馈' -FontSize 9.5 -Color $Colors.Muted | Out-Null
    Add-Box -Left 0.45 -Top 1.08 -Width 11.75 -Height 0.06 -Text '' -Fill $Colors.Blue -Line $Colors.Blue -LineWeight 0 -Radius 0 | Out-Null
    Add-Box -Left 12.45 -Top 1.08 -Width 5.6 -Height 0.06 -Text '' -Fill $Colors.Purple -Line $Colors.Purple -LineWeight 0 -Radius 0 | Out-Null
    Add-Text -Left 9.95 -Top 0.88 -Width 2.25 -Height 0.25 -Text 'PPP 主体 ≈ 2/3' -FontSize 7.5 -Bold $true -Color $Colors.BlueDark -Align 2 | Out-Null
    Add-Text -Left 15.95 -Top 0.88 -Width 2.10 -Height 0.25 -Text 'Agent 支撑 ≈ 1/3' -FontSize 7.5 -Bold $true -Color $Colors.PurpleDark -Align 2 | Out-Null

    # Major panels
    Add-SectionPanel -Left 0.35 -Top 1.28 -Width 3.35 -Height 9.70 -Fill $Colors.BlueFill -Line $Colors.Blue -Title '输入与改正数' -Subtitle 'BDS-3 PPP-B2b 服务端产品 + 用户观测与模型' -Number '1' | Out-Null
    Add-SectionPanel -Left 3.90 -Top 1.28 -Width 4.05 -Height 9.70 -Fill $Colors.GreenFill -Line $Colors.Green -Title '偏差产品生成' -Subtitle 'UPD 估计、OSB 推导与多频偏差产品' -Number '2' | Out-Null
    Add-SectionPanel -Left 8.15 -Top 1.28 -Width 4.05 -Height 9.70 -Fill $Colors.OrangeFill -Line $Colors.Orange -Title 'PPP 模糊度固定' -Subtitle '浮点解、OSB 应用、整数搜索与质量验证' -Number '3' | Out-Null
    Add-SectionPanel -Left 12.45 -Top 1.28 -Width 5.70 -Height 9.70 -Fill $Colors.PurpleFill -Line $Colors.Purple -Title 'GREAT Agent 支撑层' -Subtitle '人机交互 · 计划校验 · 服务持久化 · 执行反馈' -Number '4' | Out-Null

    # Section 1: inputs and corrections
    Add-Text -Left 0.62 -Top 2.16 -Width 2.80 -Height 0.34 -Text '1.1 服务端 PPP-B2b 改正数' -FontSize 9.8 -Bold $true -Color $Colors.BlueDark | Out-Null
    $corr1 = Add-Box -Left 0.63 -Top 2.55 -Width 2.78 -Height 0.64 -Text "播发 PPP-B2b 改正数`nBDS-3 GEO 卫星 B2b 信号" -Fill $Colors.White -Line $Colors.Blue -FontSize 8.6 -Bold $true
    $corr2 = Add-Box -Left 0.63 -Top 3.34 -Width 1.32 -Height 0.58 -Text '精密轨道`n改正数' -Fill $Colors.White -Line $Colors.Blue -FontSize 8.0 -Bold $true
    $corr3 = Add-Box -Left 2.09 -Top 3.34 -Width 1.32 -Height 0.58 -Text '精密钟差`n改正数' -Fill $Colors.White -Line $Colors.Blue -FontSize 8.0 -Bold $true
    $corr4 = Add-Box -Left 0.63 -Top 4.06 -Width 1.32 -Height 0.56 -Text 'DCB 改正数' -Fill $Colors.White -Line $Colors.Blue -FontSize 8.2 -Bold $true
    $corr5 = Add-Box -Left 2.09 -Top 4.06 -Width 1.32 -Height 0.56 -Text '电离层参数' -Fill $Colors.White -Line $Colors.Blue -FontSize 8.2 -Bold $true

    Add-Text -Left 0.62 -Top 4.78 -Width 2.80 -Height 0.34 -Text '1.2 用户观测与科研输入' -FontSize 9.8 -Bold $true -Color $Colors.BlueDark | Out-Null
    $obs1 = Add-Box -Left 0.63 -Top 5.17 -Width 2.78 -Height 0.72 -Text "RINEX / 多 GNSS 接收机数据`n15 个测站 · 按年与 DOY 组织" -Fill $Colors.White -Line $Colors.Blue -FontSize 8.5 -Bold $true
    $obs2 = Add-Box -Left 0.63 -Top 6.03 -Width 2.78 -Height 0.65 -Text "BDS-3 MEO / IGSO + GPS`n多频伪距与载波相位观测值" -Fill $Colors.White -Line $Colors.Blue -FontSize 8.4 -Bold $true
    $model = Add-Box -Left 0.63 -Top 6.82 -Width 2.78 -Height 0.82 -Text "模型与配置`n天线 · 潮汐 · ERP · 行星星历`nPPPFLT XML 模板" -Fill $Colors.DeepFill -Line $Colors.Blue -FontSize 8.1 -Bold $true
    $boundary = Add-Box -Left 0.63 -Top 7.82 -Width 2.78 -Height 0.82 -Text "输入边界`n只检查、只读取；不改写原始科研数据`n缺失输入时禁止创建任务" -Fill $Colors.White -Line $Colors.BlueDark -FontSize 7.8 -Bold $true
    $entry = Add-Box -Left 0.63 -Top 8.82 -Width 2.78 -Height 0.92 -Text "本地运行入口`n127.0.0.1 Web 页面`nPowerShell 启停 · 运行前独立预检" -Fill $Colors.White -Line $Colors.Blue -FontSize 8.0 -Bold $true

    # Input flow arrows
    Add-StepArrow -X 2.02 -Y1 3.19 -Y2 3.34 -Color $Colors.Blue
    Add-StepArrow -X 2.02 -Y1 4.62 -Y2 5.17 -Color $Colors.Blue
    Add-StepArrow -X 2.02 -Y1 5.89 -Y2 6.03 -Color $Colors.Blue
    Add-StepArrow -X 2.02 -Y1 6.68 -Y2 6.82 -Color $Colors.Blue

    # Section 2: UPD and OSB products
    Add-Text -Left 4.18 -Top 2.16 -Width 3.48 -Height 0.34 -Text '2.1 UPD 估计' -FontSize 9.8 -Bold $true -Color $Colors.GreenDark | Out-Null
    $floatServer = Add-Box -Left 4.28 -Top 2.55 -Width 3.28 -Height 0.70 -Text "UD / UC PPP 浮点解`n非差非组合 · 多 GNSS" -Fill $Colors.White -Line $Colors.Green -FontSize 9.0 -Bold $true
    $floatAmb = Add-Box -Left 4.28 -Top 3.46 -Width 3.28 -Height 0.58 -Text "浮点模糊度  B1/B2/B3 · L1/L2/L5" -Fill $Colors.White -Line $Colors.Green -FontSize 8.5 -Bold $true
    $linear = Add-Box -Left 4.28 -Top 4.25 -Width 3.28 -Height 0.58 -Text 'WL / EWL / NL 线性组合' -Fill $Colors.White -Line $Colors.Green -FontSize 8.7 -Bold $true
    $upd = Add-Box -Left 4.28 -Top 5.04 -Width 3.28 -Height 0.70 -Text "UPD 估计`n无几何网络模型" -Fill $Colors.White -Line $Colors.Green -FontSize 8.8 -Bold $true
    Add-StepArrow -X 5.92 -Y1 3.25 -Y2 3.46 -Color $Colors.Green
    Add-StepArrow -X 5.92 -Y1 4.04 -Y2 4.25 -Color $Colors.Green
    Add-StepArrow -X 5.92 -Y1 4.83 -Y2 5.04 -Color $Colors.Green

    Add-Line -X1 4.20 -Y1 6.00 -X2 7.66 -Y2 6.00 -Color $Colors.Green -Weight 1.0 -Arrow $false -Dashed $true | Out-Null
    Add-Text -Left 4.18 -Top 6.12 -Width 3.48 -Height 0.34 -Text '2.2 OSB 推导与产品发布' -FontSize 9.8 -Bold $true -Color $Colors.GreenDark | Out-Null
    $pseudoOsb = Add-Box -Left 4.28 -Top 6.55 -Width 1.52 -Height 0.72 -Text "伪距 OSB`n由 DCB 获得" -Fill $Colors.White -Line $Colors.Green -FontSize 8.2 -Bold $true
    $phaseOsb = Add-Box -Left 6.04 -Top 6.55 -Width 1.52 -Height 0.72 -Text "相位 OSB`n由 UPD + 伪距 OSB 获得" -Fill $Colors.White -Line $Colors.Green -FontSize 7.6 -Bold $true
    $osbProduct = Add-Box -Left 4.28 -Top 7.61 -Width 3.28 -Height 0.82 -Text "多频 OSB 产品`nBDS-3 + GPS · 按日/频点发布" -Fill $Colors.DeepFill -Line $Colors.GreenDark -FontSize 8.8 -Bold $true -LineWeight 1.8
    Add-StepArrow -X 5.04 -Y1 7.27 -Y2 7.61 -Color $Colors.Green
    Add-StepArrow -X 6.80 -Y1 7.27 -Y2 7.61 -Color $Colors.Green
    Add-ElbowArrow -X1 5.92 -Y1 5.74 -XMid 7.78 -X2 6.80 -Y2 6.55 -Color $Colors.Green -Weight 1.4
    $release = Add-Box -Left 4.28 -Top 8.68 -Width 3.28 -Height 0.80 -Text "产品安全发布`n版本化输出 · 只写 Run Storage`nXML · 日志 · 偏差产品" -Fill $Colors.White -Line $Colors.Green -FontSize 8.0 -Bold $true
    Add-StepArrow -X 5.92 -Y1 8.43 -Y2 8.68 -Color $Colors.Green

    # Section 3: PPP ambiguity fixing
    Add-Text -Left 8.43 -Top 2.16 -Width 3.48 -Height 0.34 -Text '3.1 浮点解与状态估计' -FontSize 9.8 -Bold $true -Color $Colors.OrangeDark | Out-Null
    $floatUser = Add-Box -Left 8.53 -Top 2.55 -Width 3.28 -Height 0.68 -Text "UD / UC PPP 浮点模型`n卫星钟差/轨道 + 对流层 + 电离层" -Fill $Colors.White -Line $Colors.Orange -FontSize 8.5 -Bold $true
    $state = Add-Box -Left 8.53 -Top 3.42 -Width 3.28 -Height 0.66 -Text "状态估计`n接收机钟差 · ZTD · 模糊度 · 坐标" -Fill $Colors.White -Line $Colors.Orange -FontSize 8.4 -Bold $true
    Add-StepArrow -X 10.17 -Y1 3.23 -Y2 3.42 -Color $Colors.Orange

    Add-Text -Left 8.43 -Top 4.24 -Width 3.48 -Height 0.34 -Text '3.2 OSB 应用' -FontSize 9.8 -Bold $true -Color $Colors.OrangeDark | Out-Null
    $applyPseudo = Add-Box -Left 8.53 -Top 4.63 -Width 3.28 -Height 0.56 -Text '伪距 OSB → 伪距观测值' -Fill $Colors.White -Line $Colors.Orange -FontSize 8.5 -Bold $true
    $applyPhase = Add-Box -Left 8.53 -Top 5.38 -Width 3.28 -Height 0.56 -Text '相位 OSB → 载波相位观测值' -Fill $Colors.White -Line $Colors.Orange -FontSize 8.5 -Bold $true
    $integerAmb = Add-Box -Left 8.53 -Top 6.13 -Width 3.28 -Height 0.58 -Text '偏差改正后的整数模糊度' -Fill $Colors.DeepFill -Line $Colors.OrangeDark -FontSize 8.8 -Bold $true -LineWeight 1.8
    Add-StepArrow -X 10.17 -Y1 4.08 -Y2 4.63 -Color $Colors.Orange
    Add-StepArrow -X 10.17 -Y1 5.19 -Y2 5.38 -Color $Colors.Orange
    Add-StepArrow -X 10.17 -Y1 5.94 -Y2 6.13 -Color $Colors.Orange

    Add-Text -Left 8.43 -Top 6.87 -Width 3.48 -Height 0.34 -Text '3.3 模糊度固定与验证' -FontSize 9.8 -Bold $true -Color $Colors.OrangeDark | Out-Null
    $search = Add-Box -Left 8.53 -Top 7.27 -Width 3.28 -Height 0.72 -Text "整数模糊度搜索`n先固定 WL/EWL，再用 LAMBDA 固定 NL" -Fill $Colors.White -Line $Colors.Orange -FontSize 8.2 -Bold $true
    $validate = Add-Box -Left 8.53 -Top 8.20 -Width 3.28 -Height 0.65 -Text "质量验证`nratio 检验 · 成功率 · 残差检验" -Fill $Colors.White -Line $Colors.Orange -FontSize 8.2 -Bold $true
    $dual = Add-Box -Left 8.53 -Top 9.11 -Width 1.50 -Height 0.62 -Text '双频固定解' -Fill $Colors.DeepFill -Line $Colors.OrangeDark -FontSize 8.5 -Bold $true -LineWeight 1.6
    $multi = Add-Box -Left 10.31 -Top 9.11 -Width 1.50 -Height 0.62 -Text '多频固定解' -Fill $Colors.DeepFill -Line $Colors.OrangeDark -FontSize 8.5 -Bold $true -LineWeight 1.6
    Add-StepArrow -X 10.17 -Y1 6.71 -Y2 7.27 -Color $Colors.Orange
    Add-StepArrow -X 10.17 -Y1 7.99 -Y2 8.20 -Color $Colors.Orange
    Add-ElbowArrow -X1 10.17 -Y1 8.85 -XMid 10.17 -X2 9.28 -Y2 9.11 -Color $Colors.Orange -Weight 1.4
    Add-ElbowArrow -X1 10.17 -Y1 8.85 -XMid 10.17 -X2 11.06 -Y2 9.11 -Color $Colors.Orange -Weight 1.4

    # PPP cross-section flows
    Add-Line -X1 3.41 -Y1 3.64 -X2 4.28 -Y2 2.90 -Color $Colors.Grey -Weight 1.4 -Arrow $true | Out-Null
    Add-Text -Left 3.45 -Top 2.79 -Width 0.72 -Height 0.27 -Text '改正数' -FontSize 6.8 -Color $Colors.Grey | Out-Null
    Add-Line -X1 3.41 -Y1 6.34 -X2 3.78 -Y2 6.34 -Color $Colors.Blue -Weight 1.5 -Arrow $false | Out-Null
    Add-Line -X1 3.78 -Y1 6.34 -X2 3.78 -Y2 2.51 -Color $Colors.Blue -Weight 1.5 -Arrow $false | Out-Null
    Add-Line -X1 3.78 -Y1 2.51 -X2 8.03 -Y2 2.51 -Color $Colors.Blue -Weight 1.5 -Arrow $false | Out-Null
    Add-Line -X1 8.03 -Y1 2.51 -X2 8.03 -Y2 2.89 -Color $Colors.Blue -Weight 1.5 -Arrow $false | Out-Null
    Add-Line -X1 8.03 -Y1 2.89 -X2 8.53 -Y2 2.89 -Color $Colors.Blue -Weight 1.5 -Arrow $true | Out-Null
    Add-Line -X1 3.41 -Y1 7.20 -X2 3.80 -Y2 7.20 -Color $Colors.Blue -Weight 1.2 -Arrow $false -Dashed $true | Out-Null
    Add-Line -X1 3.80 -Y1 7.20 -X2 3.80 -Y2 4.13 -Color $Colors.Blue -Weight 1.2 -Arrow $false -Dashed $true | Out-Null
    Add-Line -X1 3.80 -Y1 4.13 -X2 8.03 -Y2 4.13 -Color $Colors.Blue -Weight 1.2 -Arrow $false -Dashed $true | Out-Null
    Add-Line -X1 8.03 -Y1 4.13 -X2 8.03 -Y2 3.75 -Color $Colors.Blue -Weight 1.2 -Arrow $false -Dashed $true | Out-Null
    Add-Line -X1 8.03 -Y1 3.75 -X2 8.53 -Y2 3.75 -Color $Colors.Blue -Weight 1.2 -Arrow $true -Dashed $true | Out-Null
    Add-Line -X1 7.56 -Y1 7.99 -X2 8.53 -Y2 5.66 -Color $Colors.Green -Weight 1.8 -Arrow $true | Out-Null
    Add-Text -Left 7.44 -Top 6.56 -Width 1.02 -Height 0.25 -Text 'OSB 产品' -FontSize 6.8 -Bold $true -Color $Colors.GreenDark | Out-Null

    # Section 4: GREAT Agent support layer
    Add-Text -Left 12.73 -Top 2.16 -Width 5.14 -Height 0.34 -Text '4.1 用户与计划入口' -FontSize 9.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    $workbench = Add-Box -Left 12.83 -Top 2.55 -Width 2.28 -Height 0.72 -Text "React Web 工作台`n对话与参数卡片 · 计划审核" -Fill $Colors.White -Line $Colors.Purple -FontSize 8.2 -Bold $true
    $owen = Add-Box -Left 15.39 -Top 2.55 -Width 2.28 -Height 0.72 -Text "Owen 计划提案`n自然语言 → Typed Plan 候选" -Fill $Colors.White -Line $Colors.Purple -FontSize 8.1 -Bold $true
    Add-Line -X1 15.11 -Y1 2.91 -X2 15.39 -Y2 2.91 -Color $Colors.Purple -Weight 1.5 -Arrow $true | Out-Null

    $planGate = Add-Box -Left 12.83 -Top 3.48 -Width 4.84 -Height 0.88 -Text "强类型计划校验与冻结`n日期范围 · 15 测站 · 场景规则 · 输入预检`n拒绝路径、命令与未知字段；差异预览后生成 revision" -Fill $Colors.White -Line $Colors.PurpleDark -FontSize 8.2 -Bold $true -LineWeight 1.7
    Add-StepArrow -X 15.25 -Y1 3.27 -Y2 3.48 -Color $Colors.Purple

    Add-Text -Left 12.73 -Top 4.55 -Width 5.14 -Height 0.34 -Text '4.2 服务、持久化与安全边界' -FontSize 9.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    $services = Add-Box -Left 12.83 -Top 4.95 -Width 2.28 -Height 0.80 -Text "Experiment / Plan Service`n创建 · 修订 · 冻结计划" -Fill $Colors.White -Line $Colors.Purple -FontSize 8.1 -Bold $true
    $taskSvc = Add-Box -Left 15.39 -Top 4.95 -Width 2.28 -Height 0.80 -Text "Task Service`n状态机 · 停止 · 显式重试" -Fill $Colors.White -Line $Colors.Purple -FontSize 8.1 -Bold $true
    Add-Line -X1 15.11 -Y1 5.35 -X2 15.39 -Y2 5.35 -Color $Colors.Purple -Weight 1.4 -Arrow $true | Out-Null
    $storage = Add-Box -Left 12.83 -Top 5.97 -Width 2.28 -Height 0.82 -Text "SQLite + WAL`n计划 · 任务 · 尝试 · 事件" -Fill $Colors.White -Line $Colors.Purple -FontSize 8.1 -Bold $true
    $api = Add-Box -Left 15.39 -Top 5.97 -Width 2.28 -Height 0.82 -Text "HTTP 与 SSE API`n计划操作 / 状态与日志推送" -Fill $Colors.White -Line $Colors.Purple -FontSize 7.9 -Bold $true
    Add-Line -X1 15.11 -Y1 6.38 -X2 15.39 -Y2 6.38 -Color $Colors.Purple -Weight 1.4 -Arrow $true | Out-Null

    Add-Text -Left 12.73 -Top 7.02 -Width 5.14 -Height 0.34 -Text '4.3 Worker 执行链' -FontSize 9.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    $claim = Add-Box -Left 12.83 -Top 7.42 -Width 1.45 -Height 0.76 -Text "任务原子领取`npending → running" -Fill $Colors.White -Line $Colors.Purple -FontSize 7.7 -Bold $true
    $adapter = Add-Box -Left 14.52 -Top 7.42 -Width 1.45 -Height 0.76 -Text "GreatPppfltAdapter`n生成 config.xml" -Fill $Colors.White -Line $Colors.Purple -FontSize 7.4 -Bold $true
    $runner = Add-Box -Left 16.21 -Top 7.42 -Width 1.46 -Height 0.76 -Text "Process Runner`n调用 GREAT_PVT.exe" -Fill $Colors.White -Line $Colors.Purple -FontSize 7.4 -Bold $true
    Add-Line -X1 14.28 -Y1 7.80 -X2 14.52 -Y2 7.80 -Color $Colors.Purple -Weight 1.4 -Arrow $true | Out-Null
    Add-Line -X1 15.97 -Y1 7.80 -X2 16.21 -Y2 7.80 -Color $Colors.Purple -Weight 1.4 -Arrow $true | Out-Null
    $inspector = Add-Box -Left 12.83 -Top 8.43 -Width 2.28 -Height 0.78 -Text "Result Inspector`n检查 15 个非空 FLT · 分类错误" -Fill $Colors.White -Line $Colors.Purple -FontSize 7.9 -Bold $true
    $taskState = Add-Box -Left 15.39 -Top 8.43 -Width 2.28 -Height 0.78 -Text "任务状态落库`n成功 / 失败 / 取消 / 中断" -Fill $Colors.White -Line $Colors.Purple -FontSize 7.9 -Bold $true
    Add-Line -X1 15.11 -Y1 8.82 -X2 15.39 -Y2 8.82 -Color $Colors.Purple -Weight 1.4 -Arrow $true | Out-Null

    Add-Text -Left 12.73 -Top 9.43 -Width 5.14 -Height 0.34 -Text '4.4 结果反馈与可恢复闭环' -FontSize 9.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    $feedback = Add-Box -Left 12.83 -Top 9.82 -Width 4.84 -Height 0.78 -Text "实时事件流 · 运行控制 · 结果展示与下载`n隔离任务目录；异常可定位、失败可重试；计划 → 执行 → 状态 → 结果" -Fill $Colors.DeepFill -Line $Colors.PurpleDark -FontSize 8.2 -Bold $true -LineWeight 1.7

    # Agent internal flow
    Add-StepArrow -X 15.25 -Y1 4.36 -Y2 4.95 -Color $Colors.Purple
    Add-StepArrow -X 15.25 -Y1 6.79 -Y2 7.42 -Color $Colors.Purple
    Add-StepArrow -X 15.25 -Y1 9.21 -Y2 9.82 -Color $Colors.Purple

    # Agent-to-PPP integrated control and feedback links
    Add-Line -X1 12.83 -Y1 3.92 -X2 11.81 -Y2 3.75 -Color $Colors.Purple -Weight 1.5 -Arrow $true -Dashed $true | Out-Null
    Add-Text -Left 11.74 -Top 3.37 -Width 1.14 -Height 0.25 -Text '计划/参数' -FontSize 6.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    Add-Line -X1 12.83 -Y1 5.35 -X2 7.56 -Y2 8.99 -Color $Colors.Purple -Weight 1.4 -Arrow $true -Dashed $true | Out-Null
    Add-Text -Left 9.95 -Top 6.69 -Width 1.05 -Height 0.25 -Text '任务编排' -FontSize 6.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    Add-Line -X1 12.83 -Y1 7.80 -X2 11.81 -Y2 7.64 -Color $Colors.Purple -Weight 1.6 -Arrow $true | Out-Null
    Add-Text -Left 11.75 -Top 7.34 -Width 1.10 -Height 0.25 -Text '执行调用' -FontSize 6.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    Add-Line -X1 11.81 -Y1 8.53 -X2 12.83 -Y2 8.82 -Color $Colors.Purple -Weight 1.6 -Arrow $true | Out-Null
    Add-Text -Left 11.73 -Top 8.77 -Width 1.15 -Height 0.25 -Text '结果检查' -FontSize 6.8 -Bold $true -Color $Colors.PurpleDark | Out-Null
    Add-ElbowArrow -X1 12.83 -Y1 10.21 -XMid 12.58 -X2 13.97 -Y2 3.27 -Color $Colors.Purple -Weight 1.2 -Dashed $true
    Add-Text -Left 12.48 -Top 6.72 -Width 0.42 -Height 0.48 -Text "SSE`n反馈" -FontSize 6.4 -Bold $true -Color $Colors.PurpleDark | Out-Null

    # Footer legend
    Add-Text -Left 0.60 -Top 10.18 -Width 11.05 -Height 0.40 -Text '技术闭环：输入/改正数 → 偏差产品 → PPP 固定解 → Agent 结果检查与实时反馈' -FontSize 9.0 -Bold $true -Color $Colors.Ink | Out-Null
    Add-Text -Left 0.65 -Top 10.62 -Width 10.95 -Height 0.25 -Text '实线：数据或执行主链    虚线：计划、配置或反馈控制链    蓝/绿/橙：PPP 技术域    紫：Agent 支撑域' -FontSize 7.2 -Color $Colors.Muted | Out-Null

    # Bring title and main content above background by sending the background to back.
    try { $script:Page.Shapes.Item(1).SendToBack() } catch { }

    $vsdxPath = Join-Path $OutputDirectory 'GREAT_Agent_PPP_Integrated_Roadmap.vsdx'
    $pngPath = Join-Path $OutputDirectory 'GREAT_Agent_PPP_Integrated_Roadmap.png'
    $svgPath = Join-Path $OutputDirectory 'GREAT_Agent_PPP_Integrated_Roadmap.svg'

    $document.SaveAs($vsdxPath)
    $script:Page.Export($pngPath)
    $script:Page.Export($svgPath)
    $document.Close()
    $visio.Quit()

    Write-Output "VSDX=$vsdxPath"
    Write-Output "PNG=$pngPath"
    Write-Output "SVG=$svgPath"
}
catch {
    if ($null -ne $document) { try { $document.Close() } catch { } }
    if ($null -ne $visio) { try { $visio.Quit() } catch { } }
    throw
}
finally {
    if ($null -ne $script:Page) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($script:Page) }
    if ($null -ne $document) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
    if ($null -ne $visio) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($visio) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
